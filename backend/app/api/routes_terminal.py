"""Terminal SSH del panel por WebSocket.

La sesión se abre **desde el hub hacia el host SSH elegido** con una clave
dedicada (no hay contraseñas en el navegador). El WebSocket exige:

* cookie de sesión válida del administrador;
* token CSRF de esa sesión, enviado en el subprotocolo ``csrf.<token>``;
* mismo origen que el panel;
* timeout de inactividad.

El hub no acepta destinos del navegador: solo resuelve el ``host_id`` contra
el inventario de la flota y la configuración server-side ``TERMINAL_TARGETS``.
ssh-agent y los secretos no salen del servidor. La conexión SSH usa
``StrictHostKeyChecking`` mediante un ``known_hosts`` fijado.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.core import agent_terminals, fleet, security
from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger

router = APIRouter(tags=["terminal"])

#: Host que representa al propio servidor del hub.
LOCAL_HOST_ID = 1


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    await websocket.close(code=code, reason=reason)


def _targets_config() -> dict[str, dict[str, Any]]:
    """Lee el mapa JSON server-side ``TERMINAL_TARGETS``.

    Se admiten claves por id de host, nombre visible o hostname. El valor es
    un objeto con ``host``, ``port``, ``user`` y, opcionalmente, ``key`` y
    ``known_hosts``. Un JSON inválido desactiva los destinos remotos sin tumbar
    el panel: la terminal local sigue funcionando.
    """
    raw = (get_settings().TERMINAL_TARGETS or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("TERMINAL_TARGETS no es JSON válido: {}", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("TERMINAL_TARGETS debe ser un objeto JSON")
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def _target_for_host(
    host: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Resuelve el destino SSH de un host del inventario.

    Devuelve ``(target, None)`` o ``(None, motivo)``. El host local usa
    ``TERMINAL_SSH_*``; un remoto necesita una entrada en
    ``TERMINAL_TARGETS``. Nunca se acepta una IP o un usuario del navegador.
    """
    settings = get_settings()
    is_local = bool(int(host.get("is_local") or 0))

    if is_local:
        if settings.TERMINAL_LOCAL_SHELL:
            return (
                {
                    "kind": "local",
                    "host": "127.0.0.1",
                    "port": 0,
                    "username": os.environ.get("USERNAME", ""),
                    "key": "",
                    "known_hosts": "",
                },
                None,
            )
        if not settings.TERMINAL_SSH_USER:
            return None, "Terminal del hub no configurada."
        return (
            {
                "kind": "ssh",
                "host": settings.TERMINAL_SSH_HOST,
                "port": int(settings.TERMINAL_SSH_PORT),
                "username": settings.TERMINAL_SSH_USER,
                "key": settings.TERMINAL_SSH_KEY,
                "known_hosts": settings.TERMINAL_SSH_KNOWN_HOSTS,
            },
            None,
        )

    if not int(host.get("enabled") or 0):
        return None, "Host deshabilitado."
    if not host.get("online"):
        return None, "Host sin latido."

    if settings.TERMINAL_AGENT_ENABLED:
        if agent_terminals.available(int(host.get("id") or 0)):
            return (
                {
                    "kind": "agent",
                    "host": "agent",
                    "port": 0,
                    "username": "",
                    "key": "",
                    "known_hosts": "",
                },
                None,
            )
        return None, "Agente sin canal de terminal; actualiza el agente."

    targets = _targets_config()
    config = (
        targets.get(str(host.get("id")))
        or targets.get(str(host.get("name") or ""))
        or targets.get(str(host.get("hostname") or ""))
    )
    if not config:
        return None, "Sin acceso SSH configurado para este host."

    target_host = str(config.get("host") or "").strip()
    username = str(config.get("user") or settings.TERMINAL_SSH_USER).strip()
    if not target_host or not username:
        return None, "Destino SSH incompleto."
    try:
        port = int(config.get("port") or settings.TERMINAL_SSH_PORT)
    except (TypeError, ValueError):
        return None, "Puerto SSH no válido."
    key = str(config.get("key") or settings.TERMINAL_SSH_KEY)
    known_hosts = str(config.get("known_hosts") or settings.TERMINAL_SSH_KNOWN_HOSTS)
    return (
        {
            "kind": "ssh",
            "host": target_host,
            "port": port,
            "username": username,
            "key": key,
            "known_hosts": known_hosts,
        },
        None,
    )


@router.get(
    "/terminal/targets",
    summary="Hosts a los que se puede abrir una terminal SSH",
)
async def terminal_targets(request: Request) -> dict[str, Any]:
    """Lista los hosts conectados y si tienen un destino SSH disponible.

    Solo el administrador autenticado puede verla. Los hosts sin entrada en
    ``TERMINAL_TARGETS`` se devuelven como no disponibles para que el
    desplegable sea honesto: aparecen si están conectados, pero no se puede
    seleccionar una conexión que fallaría.
    """
    settings = get_settings()
    if security.get_session(
        request.cookies.get(security.SESSION_COOKIE),
        settings.SESSION_IDLE_MINUTES,
    ) is None:
        raise HTTPException(status_code=401, detail="Sesión no válida.")

    items: list[dict[str, Any]] = []
    for host in fleet.list_hosts():
        is_local = bool(int(host.get("is_local") or 0))
        if not is_local and not host.get("online"):
            continue
        if not is_local and not int(host.get("enabled") or 0):
            continue
        target, reason = _target_for_host(host)
        items.append(
            {
                "id": int(host["id"]),
                "name": str(host.get("name") or host.get("hostname") or f"host-{host['id']}"),
                "is_local": is_local,
                "available": target is not None,
                "reason": reason,
                "transport": (target or {}).get("kind", "ssh"),
            }
        )
    return {"targets": items}


async def _local_terminal(
    websocket: WebSocket,
    actor: str,
    target_name: str,
    settings: Any,
) -> None:
    """Shell local del hub mediante ConPTY (sin SSH)."""
    try:
        from winpty import PtyProcess
    except ImportError:
        await websocket.send_text(
            "\r\n[Falta pywinpty en el hub: terminal local no disponible]\r\n"
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
        return

    process = None
    audit("terminal.open", actor=actor, target=target_name)
    try:
        process = PtyProcess.spawn(
            settings.TERMINAL_LOCAL_SHELL_COMMAND,
            dimensions=(24, 80),
        )

        async def pump_output() -> None:
            try:
                while True:
                    chunk = await asyncio.to_thread(process.read, 4096)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8", "replace")
                    await websocket.send_bytes(chunk)
            except (EOFError, OSError, WebSocketDisconnect):
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
                            await asyncio.to_thread(process.write, data)
                    elif kind == "resize":
                        cols = max(20, min(300, int(payload.get("cols", 80))))
                        rows = max(5, min(120, int(payload.get("rows", 24))))
                        await asyncio.to_thread(process.setwinsize, rows, cols)
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    await websocket.send_text(
                        "\r\n[sesi?n cerrada por inactividad]\r\n"
                    )
            except WebSocketDisconnect:
                pass
            finally:
                with contextlib.suppress(Exception):
                    process.terminate()
                with contextlib.suppress(Exception):
                    await websocket.close()

        await asyncio.gather(pump_output(), pump_input(), return_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Terminal local no disponible: {}", exc)
        with contextlib.suppress(Exception):
            await websocket.send_text(
                f"\r\n[No se pudo abrir la terminal local: {exc}]\r\n"
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
        audit("terminal.close", actor=actor, target=target_name)


async def _agent_terminal(
    websocket: WebSocket,
    actor: str,
    host_id: int,
    target_name: str,
    settings: Any,
) -> None:
    """Relay entre el WebSocket del navegador y el canal PTY del agente."""
    connection = agent_terminals.get(host_id)
    if connection is None:
        await websocket.send_text(
            "\r\n[El agente no tiene el canal de terminal; actual?zalo]\r\n"
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
        return

    session_id = uuid.uuid4().hex
    try:
        queue = await connection.open_session(session_id, cols=80, rows=24)
    except Exception as exc:  # noqa: BLE001
        await websocket.send_text(f"\r\n[No se pudo abrir la terminal: {exc}]\r\n")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
        return

    audit("terminal.open", actor=actor, target=target_name)

    async def pump_agent() -> None:
        while True:
            message = await queue.get()
            kind = message.get("type")
            if kind == "output":
                data = str(message.get("data", ""))
                if data:
                    await websocket.send_bytes(data.encode("utf-8", "replace"))
            elif kind in {"exit", "error"}:
                reason = str(message.get("reason") or message.get("message") or "")
                if reason:
                    with contextlib.suppress(Exception):
                        await websocket.send_text(f"\r\n[{reason}]\r\n")
                with contextlib.suppress(Exception):
                    await websocket.close(
                        code=1000 if kind == "exit" else 1011
                    )
                return

    async def pump_browser() -> None:
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
                        await connection.send(
                            {
                                "type": "input",
                                "session": session_id,
                                "data": data,
                            }
                        )
                elif kind == "resize":
                    cols = max(20, min(300, int(payload.get("cols", 80))))
                    rows = max(5, min(120, int(payload.get("rows", 24))))
                    await connection.send(
                        {
                            "type": "resize",
                            "session": session_id,
                            "cols": cols,
                            "rows": rows,
                        }
                    )
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                await websocket.send_text("\r\n[sesi?n cerrada por inactividad]\r\n")
        except WebSocketDisconnect:
            pass
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    try:
        await asyncio.gather(pump_agent(), pump_browser(), return_exceptions=True)
    finally:
        await connection.close_session(session_id)
        with contextlib.suppress(Exception):
            await websocket.close()
        audit("terminal.close", actor=actor, target=target_name)


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
    request_host = websocket.headers.get("host") or ""
    if not origin or urlparse(origin).netloc != request_host:
        await _reject(websocket, 4403, "Origen no permitido.")
        return

    await websocket.accept(subprotocol="nexus-terminal")
    actor = str(session.get("username", "admin"))

    try:
        host_id = int(websocket.query_params.get("host") or LOCAL_HOST_ID)
    except (TypeError, ValueError):
        host_id = LOCAL_HOST_ID

    host = fleet.get_host(host_id)
    if host is None:
        await websocket.send_text("\r\n[Host no encontrado en el inventario]\r\n")
        await websocket.close(code=1011)
        return

    target, reason = _target_for_host(host)
    if target is None:
        await websocket.send_text(f"\r\n[{reason or 'Terminal no disponible'}]\r\n")
        await websocket.close(code=1011)
        return

    if target.get("kind") == "local":
        await _local_terminal(
            websocket,
            actor,
            str(host.get("name") or host_id),
            settings,
        )
        return

    if target.get("kind") == "agent":
        await _agent_terminal(
            websocket,
            actor,
            int(host_id),
            str(host.get("name") or host_id),
            settings,
        )
        return

    try:
        import asyncssh
    except ImportError:
        await websocket.send_text("\r\n[Falta la dependencia asyncssh en el hub]\r\n")
        await websocket.close(code=1011)
        return

    connection = None
    process = None
    audit_target = str(host.get("name") or host_id)
    audit("terminal.open", actor=actor, target=audit_target)
    try:
        connection = await asyncssh.connect(
            target["host"],
            port=target["port"],
            username=target["username"],
            client_keys=[target["key"]],
            known_hosts=target["known_hosts"],
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
            await websocket.send_text(f"\r\n[No se pudo abrir SSH en {audit_target}: {exc}]\r\n")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()
        audit("terminal.close", actor=actor, target=audit_target)
