"""Canal de terminal que viaja por la conexión saliente del agente.

El agente abre un WebSocket contra el hub; este módulo mantiene el registro
por host y multiplexa una sesión PTY por host. El navegador nunca habla con el
agente: habla con el hub, que reenvía el flujo autenticado.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging_conf import logger

_connections: dict[int, "AgentTerminalConnection"] = {}


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split(".")[:3])
    except (TypeError, ValueError):
        return (0,)


class AgentTerminalConnection:
    """Una conexión WSS de agente y sus sesiones PTY."""

    def __init__(
        self,
        host_id: int,
        host_name: str,
        version: str,
        websocket: WebSocket,
    ) -> None:
        self.host_id = int(host_id)
        self.host_name = host_name
        self.version = version
        self.websocket = websocket
        self.sessions: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    @property
    def supports_terminal(self) -> bool:
        return _version_tuple(self.version) >= (1, 1, 0)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def open_session(
        self,
        session_id: str,
        cols: int = 80,
        rows: int = 24,
    ) -> asyncio.Queue[dict[str, Any]]:
        if not self.supports_terminal:
            raise RuntimeError("El agente no soporta terminal; actualízalo.")
        if self.sessions:
            raise RuntimeError("Ese host ya tiene una terminal abierta.")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sessions[session_id] = queue
        await self.send(
            {
                "type": "open",
                "session": session_id,
                "cols": cols,
                "rows": rows,
            }
        )
        try:
            first = await asyncio.wait_for(queue.get(), timeout=10)
        except asyncio.TimeoutError as exc:
            self.sessions.pop(session_id, None)
            raise RuntimeError("El agente no abrió la terminal a tiempo.") from exc
        if first.get("type") != "ready":
            self.sessions.pop(session_id, None)
            raise RuntimeError(str(first.get("message") or "El agente rechazó la terminal."))
        return queue

    async def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        try:
            await self.send({"type": "close", "session": session_id})
        except Exception:
            pass

    async def reader(self) -> None:
        """Reparte mensajes del agente a la cola de su sesión."""
        try:
            async for raw in self.websocket.iter_text():
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                session_id = str(message.get("session") or "")
                queue = self.sessions.get(session_id)
                if queue is not None:
                    await queue.put(message)
                elif message.get("type") == "error":
                    logger.warning(
                        "Agente {} reportó un error de terminal: {}",
                        self.host_id,
                        message.get("message"),
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unregister(self)
            for queue in list(self.sessions.values()):
                await queue.put({"type": "exit", "reason": "Conexión del agente cerrada."})
            self.sessions.clear()


def register(connection: AgentTerminalConnection) -> None:
    old = _connections.get(connection.host_id)
    _connections[connection.host_id] = connection
    if old is not None and old is not connection:
        # El agente se ha reconectado: cerramos la conexión anterior sin
        # bloquear al nuevo.
        asyncio.create_task(old.websocket.close(code=1012))


def unregister(connection: AgentTerminalConnection) -> None:
    if _connections.get(connection.host_id) is connection:
        _connections.pop(connection.host_id, None)


def get(host_id: int) -> AgentTerminalConnection | None:
    connection = _connections.get(int(host_id))
    if connection is not None and not connection.supports_terminal:
        return None
    return connection


def available(host_id: int) -> bool:
    return get(host_id) is not None


__all__ = [
    "AgentTerminalConnection",
    "available",
    "get",
    "register",
    "unregister",
]
