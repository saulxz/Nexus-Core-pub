"""Flota de hosts: inventario, tokens de agente y cola de comandos.

Este módulo es el propietario de los datos de la parte multi-host del hub
(la tabla ``hosts`` y la cola ``commands``, ambas creadas por la migración 5 de
:mod:`app.core.db`). Es puramente de datos: no conoce FastAPI ni los agentes.

Modelo de credenciales (mismo enfoque que ``app.core.api_tokens``):

* Cada agente se **enrola** una vez con el *token de enrolamiento* del hub y
  recibe un **token de agente** que presenta en cada petición como
  ``Authorization: Bearer <token>``.
* El token en claro se devuelve **una única vez** (respuesta del enrolamiento);
  en la base de datos solo queda su SHA-256 y un prefijo legible. Un robo de la
  base de datos no da acceso utilizable.
* No hay CSRF ni cookie: es un token que posee quien lo usa, igual que una clave
  de API, porque los agentes no son navegadores.

Estados: un host es ``online`` si su último ``last_seen_ts`` es más reciente que
:data:`ONLINE_WINDOW_SECONDS`; el panel marca ``offline`` al resto. El host
local (id :data:`app.core.db.LOCAL_HOST_ID`) lo refresca el colector local, de
modo que aparece en línea mientras el servicio está arrancado.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.core.paths import config_dir

#: Prefijo reconocible de los tokens de agente.
TOKEN_PREFIX = "nxa_"

#: Longitud del prefijo legible guardado junto al hash.
PREFIX_LENGTH = 10

#: Longitud del secreto generado (bytes, antes de ``urlsafe_b64encode``).
SECRET_BYTES = 32

#: Ventana (segundos) por defecto para considerar que un host está en línea
#: (sobrescribible con ``AGENT_ONLINE_WINDOW``).
ONLINE_WINDOW_SECONDS = 30


def online_window() -> int:
    """Ventana de conexión efectiva, en segundos (mínimo 5)."""
    return max(5, int(get_settings().AGENT_ONLINE_WINDOW or ONLINE_WINDOW_SECONDS))

#: Estados posibles de un comando en la cola.
COMMAND_STATUSES: tuple[str, ...] = ("pending", "running", "done", "error", "expired")


def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    """SHA-256 en hexadecimal del token presentado por el agente."""
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


# ========================== Token de enrolamiento ==========================
def _enroll_token_path():
    """Ruta del fichero con el token de enrolamiento generado (0600)."""
    return config_dir() / "agent_enroll_token"


def enrollment_token(create: bool = True) -> str:
    """Devuelve el token con el que los agentes se enrolan en este hub.

    Precedencia:

    1. ``AGENT_ENROLL_TOKEN`` del entorno/``.env`` (lo fija el operador).
    2. El fichero ``$NEXUS_DATA_DIR/config/agent_enroll_token`` (0600), que se
       genera la primera vez y se reutiliza en los siguientes arranques.

    Es un **secreto**: quien lo posee puede dar de alta un host en la flota. Por
    eso solo se expone por la API autenticada del panel.
    """
    configured = get_settings().AGENT_ENROLL_TOKEN.strip()
    if configured:
        return configured
    path = _enroll_token_path()
    if not path.exists() and create:
        path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - en Windows los permisos POSIX no aplican
            pass
        logger.info("Token de enrolamiento de agentes generado en {}", path)
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def check_enrollment_token(candidate: str) -> bool:
    """Compara en tiempo constante el token presentado con el del hub."""
    expected = enrollment_token(create=False)
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), expected)


def get_settings_source() -> str:
    """De dónde procede el token de enrolamiento: ``entorno`` o ``generado``."""
    return "entorno" if get_settings().AGENT_ENROLL_TOKEN.strip() else "generado"


# ================================ Hosts ==================================
def _host_to_dict(row: Any, now: float | None = None) -> dict[str, Any]:
    """Fila de ``hosts`` como diccionario con el estado calculado."""
    host = db.row_to_dict(row)
    host.pop("token_hash", None)  # El hash nunca sale de la base de datos.
    host["is_local"] = int(host.get("is_local") or 0)
    host["enabled"] = int(host.get("enabled") or 0)
    host["token_prefix"] = host.get("token_prefix")
    host["online"] = is_online(host, now)
    host["status"] = computed_status(host, now)
    host["last_seen_ts"] = host.get("last_seen_ts")
    host["seconds_since_seen"] = (
        None
        if host["last_seen_ts"] is None
        else round(max(0.0, (time.time() if now is None else now) - float(host["last_seen_ts"])), 1)
    )
    return host


def is_online(host: dict[str, Any], now: float | None = None) -> bool:
    """True si el host se ha visto hace menos de la ventana de conexión."""
    last_seen = host.get("last_seen_ts")
    if last_seen is None:
        return False
    moment = time.time() if now is None else float(now)
    return (moment - float(last_seen)) <= online_window()


def computed_status(host: dict[str, Any], now: float | None = None) -> str:
    """Estado mostrado en el panel: ``disabled`` | ``online`` | ``offline``."""
    if not int(host.get("enabled") or 0):
        return "disabled"
    return "online" if is_online(host, now) else "offline"


def list_hosts(now: float | None = None) -> list[dict[str, Any]]:
    """Inventario de la flota (el host local primero, luego por id)."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM hosts ORDER BY is_local DESC, id ASC"
        ).fetchall()
    finally:
        conn.close()
    return [_host_to_dict(row, now) for row in rows]


def get_host(host_id: int) -> dict[str, Any] | None:
    """Un host por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM hosts WHERE id = ?", (int(host_id),)).fetchone()
    finally:
        conn.close()
    return _host_to_dict(row) if row is not None else None


def create_host(
    name: str,
    hostname: str | None = None,
    os_name: str | None = None,
    agent_version: str | None = None,
) -> dict[str, Any]:
    """Da de alta un host y devuelve ``{"token": <secreto>, "host": {...}}``.

    El secreto del token **solo existe en este retorno**: a partir de aquí la
    base de datos conserva su SHA-256 y su prefijo.
    """
    clean_name = (name or "").strip() or "Host sin nombre"
    secret = TOKEN_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
    stamp = _now_iso()
    conn = db.connect()
    try:
        host_id = db.next_id(conn, "hosts")
        conn.execute(
            "INSERT INTO hosts (id, name, hostname, os, agent_version, token_hash, "
            "token_prefix, status, enrolled_at, enabled, is_local) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'offline', ?, 1, 0)",
            (
                host_id,
                clean_name[:120],
                (hostname or "").strip()[:120] or None,
                (os_name or "").strip()[:120] or None,
                (agent_version or "").strip()[:40] or None,
                hash_token(secret),
                secret[:PREFIX_LENGTH],
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Host {} enrolado: {} ({})", host_id, clean_name, hostname or "sin hostname")
    return {"token": secret, "host": get_host(host_id) or {}}


def update_host(host_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Actualiza nombre, notas y/o enabled de un host.

    Solo se tocan las columnas presentes en ``changes`` (la API las obtiene con
    ``model_dump(exclude_unset=True)``).
    """
    fields: list[str] = []
    values: list[Any] = []
    for column in ("name", "notes"):
        if column in changes and changes[column] is not None:
            fields.append(f"{column} = ?")
            values.append(str(changes[column])[:200])
    if changes.get("enabled") is not None:
        fields.append("enabled = ?")
        values.append(1 if changes["enabled"] else 0)
    if fields:
        values.append(int(host_id))
        conn = db.connect()
        try:
            conn.execute(f"UPDATE hosts SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
    return get_host(host_id)


def delete_host(host_id: int) -> bool:
    """Elimina un host y **revoca su token** con ello (ya no autentica).

    También borra sus comandos, su instantánea y su histórico de métricas: el
    host ya no existe, así que sus datos no tienen sentido. El host local no se
    puede eliminar (es el servidor del panel: su ``id = 1`` es el «este
    servidor» del que dependen las métricas locales).
    """
    host = get_host(host_id)
    if host is None or host["is_local"]:
        return False
    conn = db.connect()
    try:
        conn.execute("DELETE FROM commands WHERE host_id = ?", (int(host_id),))
        conn.execute("DELETE FROM host_state WHERE host_id = ?", (int(host_id),))
        conn.execute("DELETE FROM metric_rollups WHERE host_id = ?", (int(host_id),))
        conn.execute("DELETE FROM metric_samples WHERE host_id = ?", (int(host_id),))
        removed = conn.execute("DELETE FROM hosts WHERE id = ?", (int(host_id),)).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


def touch(
    host_id: int,
    *,
    hostname: str | None = None,
    os_name: str | None = None,
    agent_version: str | None = None,
    now: float | None = None,
) -> None:
    """Refresca ``last_seen_ts``/``status`` (y los datos que informa el agente)."""
    moment = int(time.time() if now is None else now)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE hosts SET last_seen_ts = ?, status = 'online', "
            "hostname = COALESCE(?, hostname), os = COALESCE(?, os), "
            "agent_version = COALESCE(?, agent_version) WHERE id = ?",
            (
                moment,
                (hostname or "").strip()[:120] or None,
                (os_name or "").strip()[:120] or None,
                (agent_version or "").strip()[:40] or None,
                int(host_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def touch_local(now: float | None = None) -> None:
    """Marca el host local en línea (lo llama el colector de métricas).

    Se escribe como mucho una vez por ventana de conexión: el colector muestrea
    cada 5 s y en la SD de una Raspberry Pi no tiene sentido escribir (ni
    gastar) en cada muestra.
    """
    moment = float(time.time() if now is None else now)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT last_seen_ts FROM hosts WHERE id = ?", (db.LOCAL_HOST_ID,)
        ).fetchone()
        if row is not None and row["last_seen_ts"] is not None:
            if moment - float(row["last_seen_ts"]) < online_window():
                return
        conn.execute(
            "UPDATE hosts SET last_seen_ts = ?, status = 'online' WHERE id = ?",
            (int(moment), db.LOCAL_HOST_ID),
        )
        conn.commit()
    finally:
        conn.close()


def save_state(host_id: int, payload: dict[str, Any], now: float | None = None) -> None:
    """Guarda la última instantánea conocida del host (``host_state``)."""
    moment = int(time.time() if now is None else now)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO host_state (host_id, ts, payload_json) VALUES (?, ?, ?)",
            (int(host_id), moment, json.dumps(payload, ensure_ascii=False, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


# ========================= Autenticación del agente =======================
def authenticate(token: str) -> dict[str, Any] | None:
    """Valida un token de agente y devuelve el host asociado.

    Busca por hash (índice único) y descarta los hosts deshabilitados: un host
    desactivado en el panel deja de reportar de inmediato, sin esperar a que
    caduque nada.
    """
    cleaned = (token or "").strip()
    if not cleaned.startswith(TOKEN_PREFIX):
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM hosts WHERE token_hash = ?", (hash_token(cleaned),)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not int(row["enabled"] or 0):
        if row is not None:
            logger.warning("Rechazado el agente del host {}: host deshabilitado", row["id"])
        return None
    return _host_to_dict(row)


# ============================== Comandos =================================
def _command_to_dict(row: Any) -> dict[str, Any]:
    """Fila de ``commands`` como diccionario (``args``/``result`` deserializados)."""
    command = db.row_to_dict(row)
    raw_args = command.get("args_json")
    raw_result = command.get("result_json")
    try:
        command["args"] = json.loads(raw_args) if raw_args else {}
    except (json.JSONDecodeError, TypeError):  # pragma: no cover - dato corrupto
        command["args"] = {}
    try:
        command["result"] = json.loads(raw_result) if raw_result else None
    except (json.JSONDecodeError, TypeError):  # pragma: no cover - dato corrupto
        command["result"] = None
    return command


def create_command(
    host_id: int, tool: str, args: dict[str, Any] | None = None, actor: str = "system"
) -> dict[str, Any]:
    """Encola un comando para un host y devuelve la fila creada."""
    conn = db.connect()
    try:
        command_id = db.next_id(conn, "commands")
        conn.execute(
            "INSERT INTO commands (id, host_id, tool, args_json, status, actor, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (
                command_id,
                int(host_id),
                (tool or "").strip()[:80],
                json.dumps(args or {}, ensure_ascii=False, default=str),
                actor,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Comando {} encolado para el host {} ({}): {}", command_id, host_id, tool, actor)
    return get_command(command_id) or {}


def get_command(command_id: int) -> dict[str, Any] | None:
    """Un comando por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM commands WHERE id = ?", (int(command_id),)).fetchone()
    finally:
        conn.close()
    return _command_to_dict(row) if row is not None else None


def next_pending_command(host_id: int) -> dict[str, Any] | None:
    """Reserva (marca ``running``) el comando pendiente más antiguo del host.

    La reserva es atómica: el ``UPDATE`` filtra por ``status = 'pending'``, así
    que aunque dos sondeos se solapen solo uno se queda con el comando.
    """
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM commands WHERE host_id = ? AND status = 'pending' "
            "ORDER BY id ASC LIMIT 1",
            (int(host_id),),
        ).fetchone()
        if row is None:
            return None
        claimed = conn.execute(
            "UPDATE commands SET status = 'running', sent_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now_iso(), row["id"]),
        ).rowcount
        conn.commit()
        if not claimed:  # Otro sondeo se llevó el comando antes.
            return None
        reserved = conn.execute(
            "SELECT * FROM commands WHERE id = ?", (row["id"],)
        ).fetchone()
    finally:
        conn.close()
    return _command_to_dict(reserved)


def finish_command(
    command_id: int,
    status: str,
    result: Any = None,
    error: str | None = None,
    host_id: int | None = None,
) -> dict[str, Any] | None:
    """Guarda el resultado informado por el agente.

    Devuelve ``None`` si el comando no existe **o no pertenece a ese host**: un
    token robado no da acceso a la flota completa.
    """
    if status not in ("done", "error"):
        raise ValueError(f"Estado de resultado no válido: {status}")
    conn = db.connect()
    try:
        sql = (
            "UPDATE commands SET status = ?, result_json = ?, error = ?, finished_at = ? "
            "WHERE id = ?"
        )
        args: list[Any] = [
            status,
            None if result is None else json.dumps(result, ensure_ascii=False, default=str),
            (error or "")[:1000] or None,
            _now_iso(),
            int(command_id),
        ]
        if host_id is not None:
            sql += " AND host_id = ?"
            args.append(int(host_id))
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    command = get_command(command_id)
    if command is None:
        return None
    if host_id is not None and int(command["host_id"]) != int(host_id):
        return None
    return command


def expire_command(command_id: int) -> dict[str, Any] | None:
    """Marca un comando como ``expired`` (el agente no respondió a tiempo)."""
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE commands SET status = 'expired', finished_at = ? "
            "WHERE id = ? AND status IN ('pending', 'running')",
            (_now_iso(), int(command_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_command(command_id)


def list_commands(host_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Comandos recientes (de un host o de toda la flota), más recientes primero."""
    sql = "SELECT * FROM commands"
    args: list[Any] = []
    if host_id is not None:
        sql += " WHERE host_id = ?"
        args.append(int(host_id))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    conn = db.connect()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [_command_to_dict(row) for row in rows]


__all__ = [
    "COMMAND_STATUSES",
    "ONLINE_WINDOW_SECONDS",
    "PREFIX_LENGTH",
    "TOKEN_PREFIX",
    "authenticate",
    "check_enrollment_token",
    "computed_status",
    "create_command",
    "create_host",
    "delete_host",
    "enrollment_token",
    "expire_command",
    "finish_command",
    "get_command",
    "get_host",
    "hash_token",
    "is_online",
    "list_commands",
    "list_hosts",
    "next_pending_command",
    "online_window",
    "save_state",
    "touch",
    "touch_local",
    "update_host",
]