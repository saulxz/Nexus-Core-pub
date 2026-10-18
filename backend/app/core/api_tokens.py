"""Tokens de API para integraciones externas (MCP, scripts, Prometheus).

Un token es una credencial *sin sesión ni cookie*: se envía como
``Authorization: Bearer nx_...`` y permite consumir la API sin navegador.

Decisiones de seguridad:

* El token se genera con :func:`secrets.token_urlsafe` (32 bytes ≈ 256 bits de
  entropía) y se devuelve **en claro una única vez**, al crearlo.
* En la base de datos solo queda su **SHA-256** y un **prefijo de 8 caracteres**
  para que el operador pueda reconocerlo en la lista sin exponerlo. Igual que las
  contraseñas del panel, un robo de la base de datos no da acceso utilizable.
* **Ámbito** (``scope``): ``read`` solo permite métodos seguros (GET/HEAD); las
  mutaciones exigen ``write``.
* Un token **revocado** o **caducado** deja de autenticar al instante; ``DELETE``
  no borra la fila, marca ``revoked_at`` para conservar la traza en auditoría.
* ``last_used_at`` se actualiza como mucho una vez por minuto: evita escribir en
  la base de datos en cada petición sin perder la información de uso.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core import db

#: Prefijo reconocible de los tokens de Nexus-Core.
TOKEN_PREFIX = "nx_"

#: Longitud del prefijo legible que se guarda junto al hash.
PREFIX_LENGTH = 8

#: Longitud del secreto generado (en bytes, antes de ``urlsafe_b64encode``).
SECRET_BYTES = 32

#: Ámbitos admitidos por la API de tokens.
SCOPES: tuple[str, ...] = ("read", "write")

#: Intervalo mínimo entre dos actualizaciones de ``last_used_at`` (s).
_TOUCH_INTERVAL_SECONDS = 60.0

#: Caducidad por defecto de un token (días); ``None`` = sin caducidad.
DEFAULT_TTL_DAYS = 90


def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    """SHA-256 en hexadecimal del token presentado por el cliente."""
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Fila de ``api_tokens`` como diccionario (nunca incluye el hash)."""
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "prefix": str(row["prefix"]),
        "scope": str(row["scope"]),
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "active": row["revoked_at"] is None,
    }


def create(
    name: str,
    scope: str = "read",
    ttl_days: int | None = DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    """Crea un token y devuelve ``{"token", ...info}``.

    Args:
        name: Nombre descriptivo para reconocerlo en el panel.
        scope: ``read`` o ``write``.
        ttl_days: Días de validez; ``None`` o ``0`` = sin caducidad.

    Returns:
        ``{"token", "info"}``: ``token`` es el secreto en claro (única vez) e
        ``info`` son los metadatos que se pueden devolver por la API.

    Note:
        El llamante **debe** auditar la creación sin incluir el secreto.
    """
    normalized_scope = (scope or "read").strip().lower()
    if normalized_scope not in SCOPES:
        raise ValueError(f"Ámbito no válido: '{scope}'. Opciones: {', '.join(SCOPES)}.")

    secret = TOKEN_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
    token_hash = hash_token(secret)
    created_at = _now_iso()
    expires_at: str | None = None
    if ttl_days and int(ttl_days) > 0:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=int(ttl_days))
        ).isoformat(timespec="seconds")

    conn = db.connect()
    try:
        token_id = db.next_id(conn, "api_tokens")
        conn.execute(
            "INSERT INTO api_tokens (id, name, prefix, token_hash, scope, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                token_id,
                name.strip(),
                secret[: PREFIX_LENGTH],
                token_hash,
                normalized_scope,
                created_at,
                expires_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"token": secret, "info": get(token_id) or {}}


def get(token_id: int) -> dict[str, Any] | None:
    """Metadatos de un token por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row is not None else None


def list_tokens() -> list[dict[str, Any]]:
    """Lista los tokens (del más reciente al más antiguo), sin hash ni secreto."""
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM api_tokens ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def revoke(token_id: int) -> bool:
    """Revoca un token (marca ``revoked_at``; ``False`` si no existe).

    Es idempotente: revocar dos veces no falla ni cambia la fecha original.
    """
    conn = db.connect()
    try:
        removed = conn.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now_iso(), token_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


def authenticate(token: str) -> dict[str, Any] | None:
    """Valida un token presentado y devuelve la fila del token activo.

    Busca por hash (índice único), descarta los revocados y los caducados, y
    refresca ``last_used_at`` como mucho una vez por minuto.

    Returns:
        Los metadatos del token, o ``None`` si el token no sirve.
    """
    cleaned = (token or "").strip()
    if not cleaned.startswith(TOKEN_PREFIX):
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE token_hash = ?", (hash_token(cleaned),)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        expires_at = row["expires_at"]
        if expires_at and str(expires_at) <= _now_iso():
            return None
        last_used = row["last_used_at"]
        if not last_used or _seconds_since(str(last_used)) >= _TOUCH_INTERVAL_SECONDS:
            conn.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (_now_iso(), row["id"])
            )
            conn.commit()
    finally:
        conn.close()
    return _row_to_dict(row)


def _seconds_since(stamp: str) -> float:
    """Segundos transcurridos desde una marca ISO-8601 (``inf`` si es inválida)."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return float("inf")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


__all__ = [
    "DEFAULT_TTL_DAYS",
    "PREFIX_LENGTH",
    "SCOPES",
    "TOKEN_PREFIX",
    "authenticate",
    "create",
    "get",
    "hash_token",
    "list_tokens",
    "revoke",
]