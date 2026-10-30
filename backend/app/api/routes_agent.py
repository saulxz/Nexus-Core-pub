"""Protocolo del agente remoto: enrolamiento, latido, comandos y resultados.

Este router es la puerta de entrada de los **agentes** de la flota. Los agentes
se conectan **hacia fuera** (salida HTTPS al hub), de modo que un servidor
remoto detrás de NAT no necesita abrir ningún puerto: es el hub quien escucha y
el agente quien inicia la conexión.

Modelo de autenticación (deliberadamente distinto al del panel):

* ``POST /api/agent/enroll`` **no** lleva token de agente: se autentica con el
  *token de enrolamiento* del hub (``AGENT_ENROLL_TOKEN``), que es una credencial
  de instalación, no de operación. A cambio devuelve el **token de agente** en
  claro, una única vez.
* El resto de endpoints (``heartbeat``, ``commands``, ``commands/{id}/result``)
  exigen ``Authorization: Bearer <token de agente>``: se busca su SHA-256 en
  ``hosts.token_hash`` y el host debe estar habilitado.
* **No hay CSRF ni cookie**: el agente no es un navegador, es quien posee el
  token. Es el mismo modelo que una clave de API (ver
  :mod:`app.core.security`).

El heartbeat es **síncrono y barato** a propósito: una sola escritura de
muestra, el refresco del inventario y la evaluación de las reglas de alerta del
host. No hay ``asyncio.to_thread`` ni notificaciones: el bucle de eventos del
hub no debe bloquear por el tráfico de un agente (y un heartbeat cada 5 s por
host lo hace constante).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core import fleet
from app.core.audit import audit
from app.core.logging_conf import logger
from app.modules import alerts, metrics_store, notifications
from app.schemas.models import (
    AgentCommand,
    AgentCommandResult,
    AgentEnrollRequest,
    AgentEnrollResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
)

router = APIRouter(tags=["agente"])

#: Esquema Bearer. ``auto_error=False`` para responder 401 con nuestro propio
#: mensaje (y para que la ausencia de cabecera no genere un 403 ambiguo).
_bearer = HTTPBearer(auto_error=False, description="Token del agente")

#: Espera máxima del sondeo de comandos (segundos). Acota el long-poll para que
#: un proxy con tiempo de espera corto no corte la conexión.
MAX_POLL_WAIT = 60

#: Periodo recomendado al agente entre sondeos (segundos).
DEFAULT_POLL_SECONDS = 4

#: Cada cuánto se comprueba la base de datos durante el long-poll.
POLL_INTERVAL = 1.0

#: Máximo de long-polls simultáneos por host. Un agente normal usa uno.
MAX_CONCURRENT_POLLS = 2
_active_polls: dict[int, int] = {}
_poll_lock = asyncio.Lock()


def require_agent(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Dependency: autentica al agente por su token y devuelve su host.

    401 si falta el token, no es válido (inexistente o revocado) o su host está
    deshabilitado desde el panel. El host devuelto ya no lleva ``token_hash``.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Falta el token del agente (Authorization: Bearer <token>).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    host = fleet.authenticate(credentials.credentials)
    if host is None:
        logger.warning("Rechazado un token de agente inválido, revocado o de host deshabilitado")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token de agente inválido o revocado.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return host


# ============================= Enrolamiento =============================
@router.post(
    "/agent/enroll",
    response_model=AgentEnrollResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enrola un agente y devuelve su token (una sola vez)",
)
async def enroll_agent(payload: AgentEnrollRequest) -> AgentEnrollResponse:
    """Da de alta un host en la flota y le entrega su token de agente.

    Valida el token de enrolamiento del hub en comparación constante. El token
    del agente se devuelve **en claro en esta respuesta y nunca más**: el hub
    solo conserva su SHA-256.
    """
    if not fleet.check_enrollment_token(payload.token):
        audit(
            "agent.enroll",
            result="denied",
            details={"motivo": "token de enrolamiento inválido", "hostname": payload.hostname},
        )
        logger.warning("Intento de enrolamiento con token no válido desde {}", payload.hostname or "?")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El token de enrolamiento no es válido. Genéralo en el panel "
            "(GET /api/hosts/enrollment).",
        )

    name = payload.name.strip() or payload.hostname.strip() or f"Host {int(time.time())}"
    created = fleet.create_host(
        name,
        hostname=payload.hostname,
        os_name=payload.os,
        agent_version=payload.agent_version,
    )
    audit(
        "agent.enroll",
        target=f"host:{created['host']['id']}",
        result="ok",
        details={
            "nombre": name,
            "hostname": payload.hostname,
            "os": payload.os,
            "prefijo_token": created["host"]["token_prefix"],
        },
    )
    return AgentEnrollResponse(
        host_id=int(created["host"]["id"]),
        token=str(created["token"]),
        name=str(created["host"]["name"]),
        hostname=created["host"]["hostname"],
        poll_seconds=DEFAULT_POLL_SECONDS,
    )


# ================================ Latido ================================
@router.post(
    "/agent/heartbeat",
    response_model=AgentHeartbeatResponse,
    summary="Latido del agente: identidad y métricas del host",
)
async def heartbeat(
    payload: AgentHeartbeatRequest, host: dict[str, Any] = Depends(require_agent)
) -> AgentHeartbeatResponse:
    """Guarda la muestra del host, refresca el inventario y evalúa sus alertas.

    El motor de alertas se ejecuta aquí **de forma síncrona** (son pocas reglas y
    unas pocas consultas SQLite) para que la decisión llegue al agente antes de
    su siguiente latido; las notificaciones sí son ``await``-ables y se lanzan en
    segundo plano para no alargar el heartbeat.
    """
    host_id = int(host["id"])
    now = time.time()
    sample = payload.metrics.model_dump()
    ts = int(now)

    fleet.touch(
        host_id,
        hostname=payload.hostname or host.get("hostname"),
        os_name=payload.os or host.get("os"),
        agent_version=payload.agent_version or host.get("agent_version"),
        now=now,
    )
    metrics_store.insert_sample(ts, sample, host_id)
    # Los agregados cerrados los consolida el hub al ritmo de los latidos: así
    # el histórico por host existe igual que en el host local, sin un segundo
    # temporizador en el servidor.
    for period in metrics_store.ROLLUP_PERIODS:
        try:
            metrics_store.build_rollup_if_due(period, host_id, now=ts)
        except Exception as exc:  # noqa: BLE001 - el latido nunca debe fallar
            logger.warning(
                "No se pudo consolidar el agregado {} del host {}: {}", period, host_id, exc
            )

    if payload.state:
        try:
            fleet.save_state(host_id, payload.state, now=now)
        except Exception as exc:  # noqa: BLE001 - la instantánea es best-effort
            logger.warning("No se pudo guardar el estado del host {}: {}", host_id, exc)

    try:
        transitions = alerts.evaluate(sample, host_id, now=now)
    except Exception as exc:  # noqa: BLE001 - la métrica es lo importante
        logger.error("Fallo evaluando las alertas del host {}: {}", host_id, exc)
    else:
        for action, event, rule in transitions:
            # Las notificaciones van por red: no se esperan en el latido.
            asyncio.create_task(_notify_safely(action, event, rule))

    return AgentHeartbeatResponse(ok=True, host_id=host_id, ts=ts)


async def _notify_safely(action: str, event: dict[str, Any], rule: dict[str, Any]) -> None:
    """Envía la notificación de una transición sin propagar fallos."""
    try:
        await notifications.dispatch_event(action, event, rule)
    except Exception as exc:  # noqa: BLE001 - un canal no rompe el heartbeat
        logger.error("Fallo notificando la alerta {}: {}", action, exc)


# =============================== Comandos ===============================
@router.get(
    "/agent/commands",
    response_model=AgentCommand,
    summary="Sondeo de comandos (long-poll)",
    responses={204: {"description": "No hay comandos pendientes."}},
)
async def poll_commands(
    wait: int = Query(
        default=DEFAULT_POLL_SECONDS * 4,
        ge=0,
        le=MAX_POLL_WAIT,
        description="Segundos que se espera a que llegue un comando (long-poll).",
    ),
    host: dict[str, Any] = Depends(require_agent),
) -> AgentCommand | Response:
    """Devuelve el comando pendiente más antiguo del host, o 204 si no hay.

    Es un **long-poll**: en lugar de que el agente pregunte cada 5 s y el hub
    responda «nada», la conexión queda abierta hasta que hay trabajo (o hasta
    ``wait`` segundos). Así el tráfico idle es una conexión por agente y el
    despacho de un comando es inmediato.

    Al entregar el comando se marca ``running`` con su ``sent_at``: la reserva
    es atómica, así que aunque dos sondeos se solapen solo uno se lo lleva.
    """
    host_id = int(host["id"])
    async with _poll_lock:
        active = _active_polls.get(host_id, 0)
        if active >= MAX_CONCURRENT_POLLS:
            return Response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": "5"},
                content="Demasiados long-polls simultáneos para este host.",
            )
        _active_polls[host_id] = active + 1
    try:
        deadline = time.monotonic() + max(0, wait)
        while True:
            command = fleet.next_pending_command(host_id)
            if command is not None:
                logger.info(
                    "Host {} toma el comando {} ({})",
                    host_id,
                    command["id"],
                    command["tool"],
                )
                return AgentCommand(
                    id=int(command["id"]),
                    tool=str(command["tool"]),
                    args=dict(command.get("args") or {}),
                    created_at=command.get("created_at"),
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            await asyncio.sleep(min(POLL_INTERVAL, remaining))
    finally:
        async with _poll_lock:
            remaining = _active_polls.get(host_id, 1) - 1
            if remaining <= 0:
                _active_polls.pop(host_id, None)
            else:
                _active_polls[host_id] = remaining


@router.post(
    "/agent/commands/{command_id}/result",
    response_model=AgentCommand,
    summary="Devuelve el resultado de un comando ejecutado",
)
async def command_result(
    command_id: int,
    payload: AgentCommandResult,
    host: dict[str, Any] = Depends(require_agent),
) -> AgentCommand:
    """Guarda el resultado del comando indicado.

    Solo puede cerrar comandos **de su propio host**: un token robado no da
    acceso a la flota completa.
    """
    stored = fleet.finish_command(
        command_id,
        payload.status,
        payload.result,
        payload.error,
        host_id=int(host["id"]),
    )
    if stored is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Comando no encontrado: {command_id}"
        )
    audit(
        "agent.command.finished",
        actor=f"host:{host['id']}",
        target=f"command:{command_id}",
        result="ok" if payload.status == "done" else "fail",
        details={
            "herramienta": stored.get("tool"),
            "estado": stored.get("status"),
            "error": (payload.error or "")[:200] or None,
        },
    )
    return AgentCommand(
        id=int(stored["id"]),
        tool=str(stored["tool"]),
        args=dict(stored.get("args") or {}),
        created_at=stored.get("created_at"),
    )


__all__ = ["DEFAULT_POLL_SECONDS", "MAX_POLL_WAIT", "require_agent", "router"]