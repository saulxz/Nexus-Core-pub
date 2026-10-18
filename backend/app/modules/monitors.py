"""Monitores de disponibilidad: definición, heartbeats y cálculo de uptime.

Un *monitor* describe una comprobación periódica de un destino que el
administrador quiere vigilar (una web, un puerto TCP, un certificado TLS). El
comprobador (:mod:`app.modules.monitor_checker`) ejecuta esas comprobaciones en
segundo plano y graba un **heartbeat** por cada una en la tabla ``heartbeats``;
este módulo es el propietario de los datos y del cálculo de disponibilidad:

* ``monitors``: definición de cada comprobación (CRUD del panel).
* ``heartbeats``: histórico de resultados (una fila por comprobación), del que
  salen los porcentajes de 24 h / 7 d / 30 d y el estado actual.

Formato de los heartbeats por tipo de monitor:

=============  ==================================================
Columna        Contenido
=============  ==================================================
``up``         1 = la comprobación pasó, 0 = falló.
``latency_ms`` Tiempo real de la comprobación (ida + vuelta).
``status_code`` Código HTTP (solo monitores ``http``).
``detail``     ``HTTP <código>`` en http; ``host:puerto`` en tcp;
               **días restantes para caducar** (entero) en ssl.
``error``      ``TipoDeError: mensaje`` cuando la comprobación falló.
=============  ==================================================

Política de destinos (SSRF) —deliberadamente opuesta a la del webhook
(:func:`app.modules.notifications.check_webhook_url`):

* Un monitor **sí** apunta a la red interna: es la razón de ser del panel
  (``http://192.168.1.1``, ``localhost:8080``, la IP de la propia Raspberry Pi).
  Por eso **no** se rechazan las IP privadas, de loopback o link-local: son el
  caso de uso normal, y solo el administrador autenticado puede crearlos.
* Lo que sí se prohíbe es salirse del alcance del panel: esquemas distintos de
  ``http``/``https`` en los monitores http (nada de ``file://`` ni ``gopher://``)
  y destinos TCP/SSL mal formados. No se resuelve DNS ni se filtra por IP.
* Las peticiones salientes **nunca siguen redirecciones** (``follow_redirects``),
  de modo que un 30x no puede desviar la comprobación a otro destino.
* La respuesta se lee como máximo hasta ``MAX_BODY_BYTES`` para no ahogar la
  memoria de la SD con un cuerpo enorme, y toda petición tiene timeout.

La exposición pública (``GET /api/status/public``) nunca incluye ``target``,
``port``, ``keyword`` ni ``error``: solo el nombre elegido por el administrador,
el tipo de comprobación, el estado y los porcentajes de disponibilidad.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from app.core import db
from app.core.logging_conf import logger

#: Tipos de monitor soportados.
MONITOR_KINDS: tuple[str, ...] = ("http", "tcp", "ssl")

#: Límites de la definición de un monitor (los valida Pydantic en la API).
MIN_INTERVAL_S = 10
MAX_INTERVAL_S = 86400
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 30

#: Puerto asumido por los monitores ``ssl`` si el destino no lo indica.
DEFAULT_SSL_PORT = 443

#: Puertos por defecto de ``http``/``https`` cuando la URL no los lleva.
DEFAULT_HTTP_PORTS: dict[str, int] = {"http": 80, "https": 443}

#: Tamaño máximo de cuerpo leído al buscar la palabra clave (protege la SD).
MAX_BODY_BYTES = 64 * 1024

#: Máximo de heartbeats devueltos por el endpoint de histórico.
MAX_HEARTBEATS = 500

#: Cortafuegos del texto de error guardado en cada heartbeat.
MAX_ERROR_CHARS = 500

#: Ventanas de disponibilidad, en segundos (clave usada por la API y el panel).
UPTIME_WINDOWS: dict[str, int] = {
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}

#: Retención del histórico de heartbeats (90 días ≈ 130 filas por monitor/hora
#: de intervalo 60 s: unos pocos MB en la SD de la Raspberry Pi).
HEARTBEAT_RETENTION_S = 90 * 24 * 3600

#: Columnas de ``monitors`` que admiten ``NULL`` (un PATCH con null las limpia).
NULLABLE_COLUMNS: tuple[str, ...] = ("port", "expected_status", "keyword")

#: Estados posibles de un monitor en la API y en la página pública.
MONITOR_STATUSES: tuple[str, ...] = ("up", "down", "unknown")


class TargetError(ValueError):
    """El destino de un monitor no es válido para su tipo."""


class ParsedTarget(NamedTuple):
    """Destino de un monitor ya normalizado.

    Attributes:
        host: nombre de host o dirección IP sin corchetes.
        port: puerto efectivo de la comprobación (el de la URL, el del campo
            ``port`` o el del esquema en su defecto).
        url: cadena a usar en la petición (URL http o ``host:puerto``).
    """

    host: str
    port: int
    url: str


# ============================== Utilidades ==============================
def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_from_ts(ts: int) -> str:
    """Marca temporal Unix -> ISO-8601 UTC (segundos, ``+00:00``).

    Mismo formato que el resto del panel (ver ``app.modules.alerts._now_iso``),
    para que el frontend pueda parsear todas las fechas por igual.
    """
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _check_port(port: int) -> int:
    """Valida un puerto explícito (1-65535)."""
    if not 1 <= int(port) <= 65535:
        raise TargetError(f"El puerto debe estar entre 1 y 65535 (recibido: {port}).")
    return int(port)


# =========================== Destinos (parsing) ==========================
def _parse_http(target: str, port: int | None) -> ParsedTarget:
    """Normaliza la URL de un monitor ``http`` (solo ``http``/``https``)."""
    parts = urlsplit(target)
    if parts.scheme not in DEFAULT_HTTP_PORTS:
        raise TargetError(
            "Un monitor http solo admite http:// o https:// "
            f"(recibido: '{parts.scheme or target[:16]}')."
        )
    if not parts.hostname:
        raise TargetError("La URL del monitor no incluye un host válido.")
    if parts.username or parts.password:
        raise TargetError("La URL del monitor no debe incluir credenciales.")
    try:
        url_port = parts.port
    except ValueError as exc:
        raise TargetError("La URL del monitor tiene un puerto no válido.") from exc
    default_port = DEFAULT_HTTP_PORTS[parts.scheme]
    resolved = (
        _check_port(port)
        if port is not None
        else (url_port if url_port is not None else default_port)
    )
    # La autoridad se reconstruye con el puerto efectivo para que la URL que se
    # pide sea siempre coherente con ParsedTarget.port (un 'port' explícito
    # sustituye al de la URL). El puerto por defecto se omite para no ensuciar la
    # URL con un ':443' innecesario.
    host = parts.hostname
    authority = f"[{host}]" if ":" in host else host
    if resolved != default_port:
        authority = f"{authority}:{resolved}"
    return ParsedTarget(
        host=host, port=resolved, url=parts._replace(netloc=authority).geturl()
    )


def _split_socket(target: str) -> tuple[str, str]:
    """Separa ``host`` y ``puerto`` de un destino ``host[:port]``.

    Acepta IPv6 entre corchetes (``[::1]:5432``); sin corchetes y con más de un
    ``:`` no se puede saber dónde acaba el host, así que se rechaza.
    """
    if target.startswith("["):
        closing = target.find("]")
        if closing < 0:
            raise TargetError("Una dirección IPv6 debe ir entre corchetes: [::1]:5432.")
        host = target[1:closing]
        rest = target[closing + 1 :]
        if not rest:
            return host, ""
        if not rest.startswith(":"):
            raise TargetError(f"Destino no válido: '{target}'.")
        return host, rest[1:]
    colons = target.count(":")
    if colons == 0:
        return target, ""
    if colons == 1:
        host, _, raw_port = target.partition(":")
        return host, raw_port
    raise TargetError(
        f"Una dirección IPv6 con puerto debe ir entre corchetes: [{target}]."
    )


def _parse_socket(
    target: str, port: int | None, default_port: int | None
) -> ParsedTarget:
    """Normaliza el destino de un monitor ``tcp``/``ssl`` (``host[:port]``)."""
    host, raw_port = _split_socket(target)
    host = host.strip()
    if not host:
        raise TargetError("El destino del monitor no incluye un host válido.")
    if any(char.isspace() for char in host) or "/" in host:
        raise TargetError(f"Destino no válido: '{target}'.")
    resolved: int | None = _check_port(port) if port is not None else None
    if resolved is None and raw_port:
        try:
            resolved = _check_port(int(raw_port))
        except ValueError as exc:
            raise TargetError(f"El puerto '{raw_port}' no es un número válido.") from exc
    if resolved is None:
        resolved = default_port
    if resolved is None:
        raise TargetError(
            "Un monitor tcp necesita un puerto (campo 'port' o 'host:puerto')."
        )
    return ParsedTarget(host=host, port=resolved, url=f"{host}:{resolved}")


def parse_target(kind: str, target: str, port: int | None = None) -> ParsedTarget:
    """Normaliza el destino de un monitor según su tipo.

    Args:
        kind: ``http`` | ``tcp`` | ``ssl``.
        target: URL (``http``) o ``host[:port]`` (``tcp``/``ssl``).
        port: puerto explícito; tiene prioridad sobre el de la URL o del destino.

    Returns:
        El destino ya normalizado.

    Raises:
        TargetError: si el destino no es válido para el tipo indicado.
    """
    text = (target or "").strip()
    if not text:
        raise TargetError("El destino del monitor no puede estar vacío.")
    if kind == "http":
        return _parse_http(text, port)
    if kind in ("tcp", "ssl"):
        return _parse_socket(text, port, DEFAULT_SSL_PORT if kind == "ssl" else None)
    raise TargetError(
        f"Tipo de monitor no soportado: '{kind}'. Opciones: {', '.join(MONITOR_KINDS)}."
    )


def validate_definition(
    kind: str, target: str, port: int | None = None
) -> ParsedTarget:
    """Valida la definición de un monitor para la API (400 si no es válida)."""
    try:
        return parse_target(kind, target, port)
    except TargetError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


# ================================= Monitores =================================
def list_monitors(enabled_only: bool = False) -> list[dict[str, Any]]:
    """Lista los monitores definidos (todos o solo los habilitados)."""
    sql = "SELECT * FROM monitors"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    conn = db.connect()
    try:
        return [db.row_to_dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def get_monitor(monitor_id: int) -> dict[str, Any] | None:
    """Devuelve un monitor por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM monitors WHERE id = ?", (int(monitor_id),)
        ).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def create_monitor(data: dict[str, Any]) -> dict[str, Any]:
    """Crea un monitor validando su destino y devuelve la fila almacenada."""
    kind = str(data["kind"])
    target = str(data["target"])
    port = data.get("port")
    port = None if port is None else int(port)
    validate_definition(kind, target, port)
    stamp = _now_iso()
    conn = db.connect()
    try:
        monitor_id = db.next_id(conn, "monitors")
        conn.execute(
            "INSERT INTO monitors (id, name, kind, target, port, interval_s, "
            "timeout_s, expected_status, keyword, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                monitor_id,
                str(data["name"]),
                kind,
                target,
                port,
                int(data.get("interval_s") or 60),
                int(data.get("timeout_s") or 5),
                None if data.get("expected_status") is None else int(data["expected_status"]),
                data.get("keyword") or None,
                1 if data.get("enabled", True) else 0,
                stamp,
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_monitor(monitor_id) or {}


def update_monitor(monitor_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Actualiza campos de un monitor existente (los ausentes no cambian).

    Tras fusionar los cambios con la fila actual se vuelve a validar el
    destino: así un ``PATCH`` que cambie ``kind`` o ``target`` no puede dejar el
    monitor en una combinación imposible (p. ej. ``http`` con un puerto TCP).

    En las columnas anulables (``port``, ``expected_status``, ``keyword``) un
    ``null`` explícito sí se escribe y limpia el valor: la API lo distingue de
    «campo no enviado» con ``model_dump(exclude_unset=True)``.
    """
    current = get_monitor(monitor_id)
    if current is None:
        return None
    merged = dict(current)
    merged.update({key: value for key, value in changes.items() if value is not None})
    validate_definition(str(merged["kind"]), str(merged["target"]), merged.get("port"))

    fields: list[str] = []
    values: list[Any] = []
    for column in (
        "name",
        "kind",
        "target",
        "port",
        "interval_s",
        "timeout_s",
        "expected_status",
        "keyword",
    ):
        if column not in changes:
            continue
        value = changes[column]
        if value is None:
            if column not in NULLABLE_COLUMNS:
                continue  # Las columnas NOT NULL no se tocan con un null.
            fields.append(f"{column} = NULL")
            continue
        if column in ("port", "interval_s", "timeout_s", "expected_status"):
            value = int(value)
        fields.append(f"{column} = ?")
        values.append(value)
    if "enabled" in changes and changes["enabled"] is not None:
        fields.append("enabled = ?")
        values.append(1 if changes["enabled"] else 0)
    if not fields:
        return get_monitor(monitor_id)
    fields.append("updated_at = ?")
    values.extend([_now_iso(), int(monitor_id)])
    conn = db.connect()
    try:
        conn.execute(f"UPDATE monitors SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return get_monitor(monitor_id)


def delete_monitor(monitor_id: int) -> bool:
    """Elimina un monitor y su histórico de heartbeats."""
    conn = db.connect()
    try:
        removed = conn.execute(
            "DELETE FROM monitors WHERE id = ?", (int(monitor_id),)
        ).rowcount
        conn.execute("DELETE FROM heartbeats WHERE monitor_id = ?", (int(monitor_id),))
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


# ================================ Heartbeats ================================
def insert_heartbeat(
    monitor_id: int,
    ts: int,
    up: bool,
    latency_ms: float | None = None,
    status_code: int | None = None,
    detail: str | None = None,
    error: str | None = None,
) -> int:
    """Guarda el resultado de una comprobación y devuelve su id."""
    conn = db.connect()
    try:
        heartbeat_id = db.next_id(conn, "heartbeats")
        conn.execute(
            "INSERT INTO heartbeats (id, monitor_id, ts, up, latency_ms, status_code, "
            "detail, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                heartbeat_id,
                int(monitor_id),
                int(ts),
                1 if up else 0,
                None if latency_ms is None else round(float(latency_ms), 2),
                None if status_code is None else int(status_code),
                (detail or None),
                None if error is None else str(error)[:MAX_ERROR_CHARS],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return heartbeat_id


def last_heartbeat(monitor_id: int) -> dict[str, Any] | None:
    """Devuelve el último heartbeat registrado de un monitor."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM heartbeats WHERE monitor_id = ? ORDER BY ts DESC, id DESC "
            "LIMIT 1",
            (int(monitor_id),),
        ).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def list_heartbeats(
    monitor_id: int,
    since_ts: int | None = None,
    limit: int = MAX_HEARTBEATS,
) -> list[dict[str, Any]]:
    """Devuelve heartbeats recientes de un monitor, **del más antiguo al más nuevo**.

    Se leen los ``limit`` más recientes y se invierten, para que el histórico
    tenga siempre el último dato al final (como el resto de series del panel).
    """
    cap = max(1, min(int(limit), MAX_HEARTBEATS))
    sql = "SELECT * FROM heartbeats WHERE monitor_id = ?"
    args: list[Any] = [int(monitor_id)]
    if since_ts is not None:
        sql += " AND ts >= ?"
        args.append(int(since_ts))
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(cap)
    conn = db.connect()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [db.row_to_dict(row) for row in reversed(rows)]


def prune_heartbeats(older_than_ts: int, now: int | None = None) -> int:
    """Borra los heartbeats anteriores a ``older_than_ts`` (retention)."""
    moment = int(now if now is not None else time.time())
    threshold = int(
        older_than_ts
        if older_than_ts > 0
        else moment - HEARTBEAT_RETENTION_S
    )
    conn = db.connect()
    try:
        removed = conn.execute(
            "DELETE FROM heartbeats WHERE ts < ?", (threshold,)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if removed:
        logger.debug("Purga de heartbeats: {} filas antiguas eliminadas", removed)
    return int(removed)


# ============================= Cálculo de uptime ============================
def uptime_percentage(monitor_id: int, seconds: int, now: int | None = None) -> float | None:
    """Porcentaje de disponibilidad de un monitor en las últimas ``seconds``.

    Returns:
        ``(checks_up / checks_total) * 100`` redondeado a dos decimales, o
        ``None`` si no hay comprobaciones en la ventana (no se inventa un 100 %).
    """
    moment = int(now if now is not None else time.time())
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(up), 0) AS ups "
            "FROM heartbeats WHERE monitor_id = ? AND ts >= ?",
            (int(monitor_id), moment - int(seconds)),
        ).fetchone()
    finally:
        conn.close()
    total = int(row["total"] or 0)
    if total == 0:
        return None
    return round(100.0 * int(row["ups"] or 0) / total, 2)


def uptime_summary(
    monitor_id: int, now: int | None = None
) -> dict[str, float | None]:
    """Porcentajes de 24 h, 7 d y 30 d de un monitor.

    Las claves llevan el prefijo ``uptime_`` porque son las que devuelve la API
    (campo ``uptime_24h`` del monitor).
    """
    moment = int(now if now is not None else time.time())
    return {
        f"uptime_{key}": uptime_percentage(monitor_id, span, now=moment)
        for key, span in UPTIME_WINDOWS.items()
    }


def ssl_days_left(beat: dict[str, Any] | None) -> int | None:
    """Días restantes de certificado guardados en el detalle de un heartbeat ssl."""
    if not beat or beat.get("detail") is None:
        return None
    try:
        return int(str(beat["detail"]).strip())
    except (TypeError, ValueError):
        return None


# ============================ Estado de un monitor ============================
def monitor_status(beat: dict[str, Any] | None) -> str:
    """Estado de un monitor a partir de su último heartbeat."""
    if not beat:
        return "unknown"
    return "up" if int(beat.get("up") or 0) == 1 else "down"


def monitor_summary(
    monitor: dict[str, Any], now: int | None = None
) -> dict[str, Any]:
    """Añade a un monitor sus valores derivados (estado, latencia y uptime).

    Devuelve una copia del monitor con ``status``, ``latency_ms``,
    ``last_check``, ``uptime_24h``/``uptime_7d``/``uptime_30d`` y
    ``ssl_days_left`` (solo para monitores ``ssl``).
    """
    monitor_id = int(monitor["id"])
    beat = last_heartbeat(monitor_id)
    summary = dict(monitor)
    summary.update(
        {
            "status": monitor_status(beat),
            "latency_ms": None if beat is None else beat.get("latency_ms"),
            "last_check": None if beat is None else iso_from_ts(int(beat["ts"])),
            "last_error": None if beat is None else beat.get("error"),
            "ssl_days_left": (
                ssl_days_left(beat) if str(monitor.get("kind")) == "ssl" else None
            ),
        }
    )
    summary.update(uptime_summary(monitor_id, now=now))
    return summary


def list_summaries(enabled_only: bool = False, now: int | None = None) -> list[dict[str, Any]]:
    """Lista de monitores con sus valores derivados (lo que ve el panel)."""
    return [monitor_summary(item, now=now) for item in list_monitors(enabled_only)]


def overall_status(statuses: list[str]) -> str:
    """Estado global del servicio a partir de los estados de los monitores.

    ``down`` si algún monitor está caído, ``degraded`` si alguno está en estado
    desconocido (nunca comprobado o sin datos aún) y ``up`` si todos están bien.
    Sin monitores la plataforma se considera operativa.
    """
    if not statuses:
        return "up"
    if "down" in statuses:
        return "down"
    return "degraded" if "unknown" in statuses else "up"


def public_status(now: int | None = None) -> dict[str, Any]:
    """Estado público del servicio **sin filtrar datos del destino**.

    Solo se exponen el nombre elegido por el administrador, el tipo de
    comprobación, el estado, la latencia y los porcentajes de disponibilidad:
    nunca la URL, el host, el puerto, la palabra clave ni el texto de error.
    """
    entries = [
        {
            "name": str(item["name"]),
            "kind": str(item["kind"]),
            "status": str(item["status"]),
            "latency_ms": item["latency_ms"],
            "last_check": item["last_check"],
            "uptime_24h": item["uptime_24h"],
            "uptime_7d": item["uptime_7d"],
        }
        for item in list_summaries(now=now)
    ]
    return {
        "overall": overall_status([entry["status"] for entry in entries]),
        "monitors": entries,
        "updated_at": _now_iso(),
    }


__all__ = [
    "DEFAULT_SSL_PORT",
    "HEARTBEAT_RETENTION_S",
    "MAX_BODY_BYTES",
    "MAX_ERROR_CHARS",
    "MAX_HEARTBEATS",
    "MIN_INTERVAL_S",
    "MIN_TIMEOUT_S",
    "MONITOR_KINDS",
    "MONITOR_STATUSES",
    "NULLABLE_COLUMNS",
    "MAX_INTERVAL_S",
    "MAX_TIMEOUT_S",
    "ParsedTarget",
    "TargetError",
    "UPTIME_WINDOWS",
    "create_monitor",
    "delete_monitor",
    "get_monitor",
    "insert_heartbeat",
    "iso_from_ts",
    "last_heartbeat",
    "list_heartbeats",
    "list_monitors",
    "list_summaries",
    "monitor_status",
    "monitor_summary",
    "overall_status",
    "parse_target",
    "prune_heartbeats",
    "public_status",
    "ssl_days_left",
    "update_monitor",
    "uptime_percentage",
    "uptime_summary",
    "validate_definition",
]