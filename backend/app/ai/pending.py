"""Acciones propuestas por el agente IA pendientes de confirmación (FASE 5).

El agente NUNCA ejecuta acciones mutantes: las *propone*. La propuesta se
guarda aquí (SQLite) con caducidad (TTL), actor y un resumen legible. La
ejecución real ocurre SOLO cuando la persona autenticada confirma desde el
panel; hasta entonces no se toca el sistema.

Defensa contra *prompt injection*: este módulo es el autorizador real
(código, no prompt). El modelo no puede confirmar nada — la confirmación
exige la sesión autenticada del administrador y la acción caduca sola.
"""

from __future__ import annotations

import contextvars
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging_conf import logger
from app.core.security import connect

#: Segundos que una propuesta permanece válida antes de caducar.
TTL_SECONDS = 120

_actor_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nexus_actor", default="desconocido"
)


def set_actor(username: str) -> None:
    """Fija el actor (usuario autenticado) del contexto actual."""
    _actor_var.set(username)


def current_actor() -> str:
    """Devuelve el actor del contexto actual."""
    return _actor_var.get()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def init_db() -> None:
    """Crea la tabla de acciones pendientes (idempotente)."""
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
                id         TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                actor      TEXT NOT NULL,
                tool       TEXT NOT NULL,
                args       TEXT NOT NULL,
                summary    TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: Any) -> dict[str, Any]:
    try:
        args = json.loads(row["args"])
    except (json.JSONDecodeError, TypeError):
        args = {}
    return {
        "id": row["id"],
        "tool": row["tool"],
        "args": args,
        "summary": row["summary"],
        "actor": row["actor"],
        "status": row["status"],
        "created_at": _iso(row["created_at"]),
        "expires_at": _iso(row["expires_at"]),
    }


def create(
    tool: str, args: dict[str, Any], summary: str, ttl: int = TTL_SECONDS
) -> dict[str, Any]:
    """Registra una propuesta pendiente de confirmación humana."""
    now = time.time()
    action_id = secrets.token_hex(8)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO pending_actions (id, created_at, expires_at, actor, tool, args, summary, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                action_id,
                now,
                now + ttl,
                current_actor(),
                tool,
                json.dumps(args, ensure_ascii=False),
                summary,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Propuesta IA creada: {} ({})", summary, action_id)
    return {
        "id": action_id,
        "tool": tool,
        "summary": summary,
        "status": "pending",
        "created_at": _iso(now),
        "expires_at": _iso(now + ttl),
        "ttl": ttl,
    }


def get(action_id: str) -> dict[str, Any] | None:
    """Devuelve una propuesta (marcándola como caducada si procede)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            return None
        if row["status"] == "pending" and time.time() > row["expires_at"]:
            conn.execute(
                "UPDATE pending_actions SET status = 'expired' WHERE id = ?",
                (action_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
            ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_for(actor: str, limit: int = 20) -> list[dict[str, Any]]:
    """Lista las propuestas recientes de un actor."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM pending_actions WHERE actor = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (actor, limit),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def confirm(action_id: str, actor: str) -> dict[str, Any]:
    """Marca una propuesta como confirmada (transición de un solo uso).

    Devuelve ``{"success", "message", "reason", "action"}``. ``reason`` es
    ``not_found`` | ``forbidden`` | ``expired`` | ``state`` | None.
    """
    now = time.time()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            return {
                "success": False,
                "message": "La acción no existe.",
                "reason": "not_found",
                "action": None,
            }
        if row["actor"] != actor:
            return {
                "success": False,
                "message": "La acción pertenece a otra sesión.",
                "reason": "forbidden",
                "action": None,
            }
        if row["status"] != "pending":
            return {
                "success": False,
                "message": f"La acción ya está en estado '{row['status']}'.",
                "reason": "state",
                "action": None,
            }
        if now > row["expires_at"]:
            conn.execute(
                "UPDATE pending_actions SET status = 'expired' WHERE id = ?",
                (action_id,),
            )
            conn.commit()
            return {
                "success": False,
                "message": "La propuesta caducó; pídesela de nuevo al agente.",
                "reason": "expired",
                "action": None,
            }
        changed = conn.execute(
            "UPDATE pending_actions SET status = 'confirmed' "
            "WHERE id = ? AND status = 'pending'",
            (action_id,),
        )
        conn.commit()
        if changed.rowcount != 1:
            return {
                "success": False,
                "message": "La acción ya no está pendiente.",
                "reason": "state",
                "action": None,
            }
        return {
            "success": True,
            "message": "Propuesta confirmada.",
            "reason": None,
            "action": _row_to_dict(row),
        }
    finally:
        conn.close()


def cancel(action_id: str, actor: str) -> dict[str, Any]:
    """Cancela una propuesta pendiente."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            return {"success": False, "message": "La acción no existe.", "reason": "not_found"}
        if row["actor"] != actor:
            return {"success": False, "message": "La acción pertenece a otra sesión.", "reason": "forbidden"}
        if row["status"] != "pending":
            return {
                "success": False,
                "message": f"La acción ya está en estado '{row['status']}'.",
                "reason": "state",
            }
        conn.execute(
            "UPDATE pending_actions SET status = 'cancelled' WHERE id = ?",
            (action_id,),
        )
        conn.commit()
        return {"success": True, "message": "Propuesta cancelada.", "reason": None}
    finally:
        conn.close()
