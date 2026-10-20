"""Terminal SSH del panel por WebSocket.

La sesión se abre **desde el hub hacia un host SSH** con una clave dedicada
(no hay contraseñas en el navegador). El WebSocket exige:

* cookie de sesión válida del administrador;
* token CSRF de esa sesión, enviado en el subprotocolo ``csrf.<token>``;
* mismo origen que el panel;
* timeout de inactividad.

ssh-agent y los secretos no salen del servidor. La conexión SSH usa
``StrictHostKeyChecking`` mediante un ``known_hosts`` fijado.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import security
from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger

router = APIRouter(tags=["terminal"])


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    await websocket.close(code=code, reason=reason)


@router.websocket("/terminal/ws")
async def terminal_socket(websocket: WebSocket) -> None:
    settings = get_settings()
    session = security.get_session(
        websocket.cookies.get(security.SESSION_COOKIE),
        settings.SESSION_IDLE_MINUTES,
    )
    if session is None:
        await _reject(websocket, 4401, "Sesión no válida.")
        return

    offered = [
        item.strip()
        for item in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if item.strip()
    ]
    csrf = next((item[5:] for item in offered if item.startswith("csrf.")), "")
    if not csrf or not hmac.compare_digest(csrf, str(session.get("csrf", ""))):
        await _reject(websocket, 4403, "CSRF no válido.")
        return

    origin = websocket.headers.get("origin") or ""
    host = websocket.headers.get("host") or ""
    if not origin or urlparse(origin).netloc != host:
        await _reject(websocket, 4403, "Origen no permitido.")
        return

    await websocket.accept(subprotocol="nexus-terminal")
    actor = str(session.get("username", "admin"))

    if not settings.TERMINAL_SSH_USER:
        await websocket.send_text(
            "\r\n[Terminal no configurada: define TERMINAL_SSH_USER y la clave SSH]\r\n"
        )
        await websocket.close(code=1011)
        return

    try:
        import asyncssh
    except ImportError:
        await websocket.send_text("\r\n[Falta la dependencia asyncssh en el hub]\r\n")
        await websocket.close(code=1011)
        return

    connection = None
    process = None
    audit("terminal.open", actor=actor, target=settings.TERMINAL_SSH_USER)
    try:
        connection = await asyncssh.connect(
            settings.TERMINAL_SSH_HOST,
            port=settings.TERMINAL_SSH_PORT,
            username=settings.TERMINAL_SSH_USER,
            client_keys=[settings.TERMINAL_SSH_KEY],
            known_hosts=settings.TERMINAL_SSH_KNOWN_HOSTS,
            config=None,
            connect_timeout=10,
            login_timeout=10,
        )
        process = await connection.create_process(
            term_type="xterm-256color",
            term_size=(80, 24),
            encoding=None,
            stderr=asyncssh.STDOUT,
        )

        async def pump_output() -> None:
            try:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    await websocket.send_bytes(chunk)
            except (ConnectionError, asyncssh.Error):
                pass
            finally:
                with contextlib.suppress(Exception):
                    await websocket.close()

        async def pump_input() -> None:
            try:
                while True:
                    message = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=settings.TERMINAL_IDLE_SECONDS,
                    )
                    if len(message) > settings.TERMINAL_MAX_MESSAGE:
                        continue
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    kind = payload.get("type")
                    if kind == "input":
                        data = str(payload.get("data", ""))
                        if data:
                            # El PTY se abrió con encoding=None: stdin espera bytes.
                            process.stdin.write(data.encode("utf-8"))
                            await process.stdin.drain()
                    elif kind == "resize":
                        cols = max(20, min(300, int(payload.get("cols", 80))))
                        rows = max(5, min(120, int(payload.get("rows", 24))))
                        process.change_terminal_size(cols, rows)
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await websocket.send_text("\r\n[sesión cerrada por inactividad]\r\n")
            except WebSocketDisconnect:
                pass
            finally:
                with contextlib.suppress(Exception):
                    process.terminate()
                with contextlib.suppress(Exception):
                    await websocket.close()

        await asyncio.gather(pump_output(), pump_input(), return_exceptions=True)
    except Exception as exc:  # noqa: BLE001 - se informa al navegador
        logger.warning("Terminal SSH no disponible: {}", exc)
        with contextlib.suppress(Exception):
            await websocket.send_text(f"\r\n[No se pudo abrir SSH: {exc}]\r\n")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        audit("terminal.close", actor=actor, target=settings.TERMINAL_SSH_USER)
