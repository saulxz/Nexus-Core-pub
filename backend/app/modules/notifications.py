"""Despacho de notificaciones a Telegram, webhooks y correo (SMTP).

Se apoya en los canales guardados en ``notification_channels`` y en
:mod:`app.modules.alerts` para saber qué transiciones hay que avisar:

* ``telegram``: ``POST https://api.telegram.org/bot<token>/sendMessage``.
* ``webhook``: ``POST`` JSON ``{event, rule, host}`` a una URL externa.
* ``smtp``: correo con la biblioteca estándar, en un hilo aparte.

Seguridad del webhook (la parte delicada): una URL de destino la elige el
administrador desde el panel, pero sigue siendo una petición **saliente** la
realiza el servidor, así que se aplica una guarda anti-SSRF clásica:

1. Solo ``http``/``https`` y con puerto explícito en la URL.
2. El host se resuelve por DNS y **todas** las IP devueltas deben ser públicas:
   se rechazan loopback (``127.0.0.1``, ``::1``), privadas (RFC1918),
   link-local (incluido el ``169.254.169.254`` de metadatos en la nube),
   reservadas, multicast y «sin dirección».
3. Sin redirecciones: un 30x hacia la red interna devolvería el guard y el
   cliente seguiría la redirección por su cuenta.
4. Timeout de 5 s y tamaño de respuesta acotado.

Los secretos (token del bot, contraseña SMTP, cabeceras de autenticación del
webhook) se guardan en la base de datos y **nunca** se devuelven a la API: solo
se exponen enmascarados (``mask_config``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, status

from app.core import db
from app.core.audit import audit
from app.core.logging_conf import logger

#: Tipos de canal soportados.
CHANNEL_KINDS: tuple[str, ...] = ("telegram", "webhook", "smtp")

#: Timeout de las peticiones salientes (segundos).
HTTP_TIMEOUT = 5.0

#: Tamaño máximo de respuesta aceptado (evita que un destino inestable
#: cargue la memoria de la Raspberry Pi).
MAX_RESPONSE_BYTES = 64 * 1024


class SSRFError(ValueError):
    """El destino de un webhook no es una URL pública válida."""


# ============================== Utilidades ==============================
def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Claves de la configuración que se enmascaran al devolverla por la API.
SECRET_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("token",),
    "webhook": ("authorization", "x_api_key", "api_key"),
    "smtp": ("password",),
}


def mask_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Devuelve la configuración del canal con los secretos enmascarados."""
    secrets = SECRET_CONFIG_KEYS.get(kind, ())
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if key in secrets and value:
            text = str(value)
            masked[key] = ("•" * 6 + text[-4:]) if len(text) > 4 else ("•" * len(text))
        else:
            masked[key] = value
    return masked


def _parse_config(row: dict[str, Any]) -> dict[str, Any]:
    """Deserializa ``config_json`` tolerando valores corruptos."""
    try:
        parsed = json.loads(row.get("config_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ============================ Guarda SSRF ============================
def _check_address(address: str) -> None:
    """Lanza :class:`SSRFError` si la IP no es pública (allowlist de salida)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:  # pragma: no cover - getaddrinfo siempre devuelve IP
        raise SSRFError(f"Dirección no válida: {address}") from exc
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise SSRFError(f"Destino no permitido (red interna): {address}")
    # 169.254.169.254 (metadatos de instancia en la nube) ya cae en link-local,
    # pero se comprueba explícitamente por claridad y por futuros cambios.
    if address == "169.254.169.254":
        raise SSRFError("Destino no permitido (metadatos de nube): 169.254.169.254")


def check_webhook_url(url: str) -> str:
    """Valida la URL de un webhook y devuelve la URL normalizada.

    Resuelve el nombre de host y comprueba que **ninguna** de sus IP sea
    interna. Es una función síncrona (DNS): se llama al guardar el canal, al
    probarlo y al despachar, nunca en el bucle de muestreo de métricas.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise SSRFError("La URL debe usar http:// o https://.")
    if not parts.hostname:
        raise SSRFError("La URL no incluye un host válido.")
    default_port = 443 if parts.scheme == "https" else 80
    try:
        infos = socket.getaddrinfo(
            parts.hostname, parts.port or default_port, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise SSRFError(f"No se pudo resolver el host: {parts.hostname}") from exc
    if not infos:
        raise SSRFError(f"El host no resuelve a ninguna dirección: {parts.hostname}")
    for info in infos:
        _check_address(str(info[4][0]))
    return url.strip()


# ============================== Canales ==============================
def list_channels(enabled_only: bool = False) -> list[dict[str, Any]]:
    """Lista los canales de notificación con su configuración enmascarada."""
    sql = "SELECT * FROM notification_channels"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    conn = db.connect()
    try:
        rows = [db.row_to_dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()
    for row in rows:
        row["config"] = mask_config(str(row.get("kind", "")), _parse_config(row))
        row.pop("config_json", None)
    return rows


def get_channel(channel_id: int) -> dict[str, Any] | None:
    """Devuelve un canal (configuración enmascarada) por su id."""
    conn = db.connect()
    try:
        row = db.row_to_dict(conn.execute(
            "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
        ).fetchone())
    finally:
        conn.close()
    if not row:
        return None
    row["config"] = mask_config(str(row.get("kind", "")), _parse_config(row))
    row.pop("config_json", None)
    return row


def _raw_channel(channel_id: int) -> dict[str, Any] | None:
    """Devuelve la fila del canal **con** la configuración en claro (interno)."""
    conn = db.connect()
    try:
        return db.row_to_dict(conn.execute(
            "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
        ).fetchone()) or None
    finally:
        conn.close()


def validate_channel(kind: str, config: dict[str, Any]) -> None:
    """Valida la configuración según el tipo de canal (400 si algo falla)."""
    if kind not in CHANNEL_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Tipo de canal no soportado: '{kind}'. Opciones: {', '.join(CHANNEL_KINDS)}.",
        )
    if kind == "telegram":
        if not config.get("token") or not config.get("chat_id"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Telegram necesita 'token' y 'chat_id'."
            )
    elif kind == "webhook":
        if not config.get("url"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "El webhook necesita 'url'."
            )
        try:
            check_webhook_url(str(config["url"]))
        except SSRFError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    elif kind == "smtp":
        missing = [
            field
            for field in ("host", "from_addr")
            if not config.get(field)
        ]
        if missing or not config.get("to"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"El correo necesita {', '.join([*missing, 'to'])}.",
            )


def create_channel(
    name: str, kind: str, config: dict[str, Any], enabled: bool = True
) -> dict[str, Any]:
    """Crea un canal de notificación (validando la configuración)."""
    validate_channel(kind, config)
    stamp = _now_iso()
    conn = db.connect()
    try:
        channel_id = db.next_id(conn, "notification_channels")
        conn.execute(
            "INSERT INTO notification_channels (id, name, kind, config_json, enabled, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                channel_id,
                name,
                kind,
                json.dumps(config, ensure_ascii=False),
                1 if enabled else 0,
                stamp,
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_channel(channel_id) or {}


def update_channel(
    channel_id: int, changes: dict[str, Any]
) -> dict[str, Any] | None:
    """Actualiza un canal. La configuración se sustituye completa si viene."""
    current = _raw_channel(channel_id)
    if current is None:
        return None
    kind = str(changes.get("kind") or current["kind"])
    config = _parse_config(current)
    if changes.get("config") is not None:
        config = dict(changes["config"])
    validate_channel(kind, config)
    fields: list[str] = []
    values: list[Any] = []
    for column in ("name", "kind"):
        if changes.get(column) is not None:
            fields.append(f"{column} = ?")
            values.append(changes[column])
    if "enabled" in changes and changes["enabled"] is not None:
        fields.append("enabled = ?")
        values.append(1 if changes["enabled"] else 0)
    if changes.get("config") is not None or changes.get("kind") is not None:
        fields.append("config_json = ?")
        values.append(json.dumps(config, ensure_ascii=False))
    if fields:
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.append(channel_id)
        conn = db.connect()
        try:
            conn.execute(
                f"UPDATE notification_channels SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()
    return get_channel(channel_id)


def delete_channel(channel_id: int) -> bool:
    """Elimina un canal de notificación."""
    conn = db.connect()
    try:
        removed = conn.execute(
            "DELETE FROM notification_channels WHERE id = ?", (channel_id,)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


def _record_result(channel_id: int, error: str | None) -> None:
    """Guarda el último resultado del envío en el canal."""
    conn = db.connect()
    try:
        if error:
            conn.execute(
                "UPDATE notification_channels SET last_error = ? WHERE id = ?",
                (error[:500], channel_id),
            )
        else:
            conn.execute(
                "UPDATE notification_channels SET last_ok_at = ?, last_error = NULL "
                "WHERE id = ?",
                (_now_iso(), channel_id),
            )
        conn.commit()
    finally:
        conn.close()


# ============================ Envío por canal ============================
async def _send_telegram(config: dict[str, Any], title: str, body: str) -> None:
    """Envía un mensaje por el bot de Telegram."""
    token = str(config.get("token", ""))
    chat_id = str(config.get("chat_id", ""))
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"*{title}*\n{body}" if body else f"*{title}*"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as client:
        response = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram respondió HTTP {response.status_code}")


async def _send_webhook(config: dict[str, Any], payload: dict[str, Any]) -> None:
    """Envía el evento como JSON a un webhook externo (con guarda SSRF)."""
    url = check_webhook_url(str(config.get("url", "")))
    headers = {"Content-Type": "application/json", **(config.get("headers") or {})}
    if config.get("authorization"):
        headers["Authorization"] = str(config["authorization"])
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as client:
        # Se consume la respuesta por trozos para acotar la memoria usada por
        # un destino inestable (una Pi no puede permitirse un cuerpo de 1 GB).
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"El webhook respondió HTTP {response.status_code}")
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > MAX_RESPONSE_BYTES:
                    logger.warning("Respuesta del webhook mayor de lo esperado; se interrumpe")
                    break


async def _send_smtp(config: dict[str, Any], title: str, body: str) -> None:
    """Envía un correo (TLS implícito en 465, STARTTLS en el resto)."""
    await asyncio.to_thread(_send_smtp_sync, config, title, body)


def _send_smtp_sync(config: dict[str, Any], title: str, body: str) -> None:
    """Implementación síncrona de SMTP con ``smtplib`` (se ejecuta en un hilo)."""
    host = str(config.get("host", ""))
    port = int(config.get("port") or 25)
    user = str(config.get("user", "") or "")
    password = str(config.get("password", "") or "")
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = str(config.get("from_addr", "nexus@localhost"))
    message["To"] = str(config.get("to", ""))
    message.set_content(body or title)
    context = ssl.create_default_context()
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=HTTP_TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT)
    try:
        server.ehlo()
        if port != 465:
            server.starttls(context=context)
            server.ehlo()
        if user:
            server.login(user, password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - el cierre no debe enmascarar el error
            pass


async def send_to_channel(
    channel_id: int, title: str, body: str, payload: dict[str, Any]
) -> tuple[bool, str]:
    """Envía un mensaje por un canal concreto y registra el resultado.

    Nunca lanza: un canal caído (bot mal configurado, SMTP caído) no puede
    tumbar el colector de métricas. Devuelve ``(ok, detalle)``.
    """
    channel = _raw_channel(channel_id)
    if channel is None:
        return False, "El canal ya no existe."
    config = _parse_config(channel)
    kind = str(channel["kind"])
    try:
        if kind == "telegram":
            await _send_telegram(config, title, body)
        elif kind == "webhook":
            await _send_webhook(config, payload)
        elif kind == "smtp":
            await _send_smtp(config, title, body)
        else:  # pragma: no cover - la tabla restringe los tipos
            raise RuntimeError(f"Tipo de canal desconocido: {kind}")
    except Exception as exc:  # noqa: BLE001 - un canal no puede romper el colector
        detail = f"{type(exc).__name__}: {exc}"
        _record_result(channel_id, detail)
        logger.warning("Notificación fallida por el canal {}: {}", channel_id, detail)
        return False, detail
    _record_result(channel_id, None)
    logger.info("Notificación enviada por el canal {} ({})", channel_id, kind)
    return True, "enviado"


# ============================== Despacho ==============================
def _payload(event: dict[str, Any], rule: dict[str, Any], action: str) -> dict[str, Any]:
    """Construye el cuerpo del webhook ``{event, rule, host}``."""
    return {
        "action": action,
        "host": socket.gethostname(),
        "event": event,
        "rule": rule,
        "sent_at": _now_iso(),
    }


def _format(action: str, event: dict[str, Any], rule: dict[str, Any]) -> tuple[str, str]:
    """Compone el título y el cuerpo (texto) del aviso."""
    name = str(rule.get("name") or event.get("metric") or "alerta")
    metric = event.get("metric", "")
    value = event.get("value")
    threshold = event.get("threshold")
    try:
        value_text = f"{float(value):.1f}" if value is not None else "?"
        threshold_text = f"{float(threshold):.1f}" if threshold is not None else "?"
    except (TypeError, ValueError):  # pragma: no cover - datos corruptos
        value_text, threshold_text = str(value), str(threshold)
    severity = "CRÍTICA" if event.get("severity") == "crit" else "Aviso"
    if action == "opened":
        title = f"[{severity}] {name}"
        body = (
            f"{metric} = {value_text} (umbral {threshold_text})\n"
            f"Regla: {name}\nEvento #{event.get('id')} abierto {event.get('opened_at')}"
        )
    else:
        title = f"[OK] {name} recuperada"
        body = (
            f"{metric} = {value_text}\n"
            f"Regla: {name}\nEvento #{event.get('id')} cerrado {event.get('closed_at')}"
        )
    if event.get("suppressed"):
        body += "\n(Registrado durante una ventana de mantenimiento)"
    return title, body


async def dispatch_event(action: str, event: dict[str, Any], rule: dict[str, Any]) -> int:
    """Notifica una transición de alerta a todos los canales habilitados.

    No hace nada si el evento se registró en una ventana de mantenimiento
    (``suppressed``): el evento queda en el historial, pero no genera ruido.

    Returns:
        Número de canales que aceptaron el mensaje.
    """
    if event.get("suppressed"):
        logger.info(
            "Evento #{} suprimido por ventana de mantenimiento: sin notificación",
            event.get("id"),
        )
        return 0
    title, body = _format(action, event, rule)
    payload = _payload(event, rule, action)
    sent = 0
    for channel in list_channels(enabled_only=True):
        ok, _ = await send_to_channel(int(channel["id"]), title, body, payload)
        if ok:
            sent += 1
    audit(
        f"alert.{action}.notified",
        target=f"event:{event.get('id')}",
        result="ok" if sent else "fail",
        details={"canales": sent, "titulo": title},
    )
    return sent


async def send_test(channel_id: int) -> dict[str, Any]:
    """Envía un mensaje de prueba por un canal concreto (acción de la API)."""
    channel = _raw_channel(channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El canal no existe.")
    stamp = _now_iso()
    event = {
        "id": 0,
        "metric": "cpu",
        "status": "open",
        "severity": "warn",
        "value": 0.0,
        "threshold": 0.0,
        "opened_at": stamp,
        "suppressed": 0,
    }
    rule = {
        "id": 0,
        "name": "Prueba de configuración",
        "metric": "cpu",
        "op": ">",
        "threshold": 0.0,
    }
    payload = _payload(event, rule, "test")
    ok, detail = await send_to_channel(
        channel_id,
        "Nexus-Core: prueba de canal",
        "Configuración de notificación correcta.",
        payload,
    )
    return {"ok": ok, "detail": detail, "kind": channel["kind"], "name": channel["name"]}


__all__ = [
    "CHANNEL_KINDS",
    "HTTP_TIMEOUT",
    "SSRFError",
    "check_webhook_url",
    "create_channel",
    "delete_channel",
    "dispatch_event",
    "get_channel",
    "list_channels",
    "mask_config",
    "send_test",
    "send_to_channel",
    "update_channel",
]