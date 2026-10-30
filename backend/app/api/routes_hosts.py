"""API de flota: inventario de hosts, enrolamiento, histórico y despacho.

Todo lo que ve aquí el **panel** (sesión del administrador o token de API con
ámbito ``write``), nunca un agente: es la contracara de
:mod:`app.api.routes_agent`. El router se registra en ``main.py`` con la
dependencia ``require_auth``.

Lo que ofrece:

* ``GET /api/hosts`` — inventario con el estado calculado (online si el último
  latido es reciente) y recuentos.
* ``PATCH``/``DELETE /api/hosts/{id}`` — renombrar, anotar, deshabilitar y
  revocar el token de un host. El host local (id 1, «este servidor») está
  protegido: no se puede borrar ni deshabilitar.
* ``GET /api/hosts/enrollment`` — el token de enrolamiento (secreto) y el
  comando exacto para instalar un agente nuevo.
* ``GET /api/hosts/{id}/metrics/history`` — histórico de un host con la misma
  forma que ``/api/metrics-history``.
* ``POST /api/hosts/{id}/command`` — el **despachador genérico**: encola un
  comando y espera su resultado. Es la base sobre la que la fase 3 manejará
  Docker y servicios de cualquier host de la flota con el mismo código que usa
  hoy el host local.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core import fleet
from app.core.audit import audit
from app.core.logging_conf import logger
from app.modules import metrics_store
from app.schemas.models import (
    ActionResponse,
    CommandDispatchResponse,
    CommandListResponse,
    CommandRequest,
    EnrollmentInfo,
    Host,
    HostListResponse,
    HostUpdate,
    MetricsHistoryResponse,
)

router = APIRouter(tags=["flota"])

#: Cada cuánto se mira la base de datos mientras se espera un comando.
DISPATCH_POLL_INTERVAL = 0.5


def _actor(request: Request) -> str:
    """Usuario autenticado (la dependencia global del router ya lo validó)."""
    return str(request.state.session["username"])


def _require_host(host_id: int) -> dict[str, Any]:
    """Devuelve un host o lanza 404 si no existe."""
    host = fleet.get_host(host_id)
    if host is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Host no encontrado: {host_id}"
        )
    return host


def _require_mutable(host: dict[str, Any]) -> None:
    """Impide tocar el host local: es el servidor donde corre el panel."""
    if int(host["is_local"]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El host local ('Este servidor') no se puede modificar ni eliminar: "
            "es la máquina que ejecuta el hub.",
        )


def _parse_metrics(raw: str) -> tuple[str, ...]:
    """Valida el parámetro ``metric=cpu,ram`` de la API de histórico."""
    names = tuple(part.strip() for part in (raw or "").split(",") if part.strip())
    if not names:
        return ("cpu", "ram")
    unknown = [name for name in names if name not in metrics_store.METRIC_COLUMNS]
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Métrica desconocida: {', '.join(unknown)}. "
            f"Opciones: {', '.join(metrics_store.METRIC_COLUMNS)}.",
        )
    return names


# =============================== Inventario ==============================
@router.get("/hosts", response_model=HostListResponse, summary="Lista los hosts de la flota")
async def list_hosts() -> HostListResponse:
    """Devuelve el inventario con el estado calculado y recuentos.

    El host local (id 1) siempre está en la lista: es el servidor del panel y su
    latido lo mantiene el colector local.
    """
    hosts = fleet.list_hosts()
    online = sum(1 for host in hosts if host["status"] == "online")
    return HostListResponse(
        count=len(hosts),
        online=online,
        offline=len(hosts) - online,
        hosts=[Host(**host) for host in hosts],
    )


@router.patch("/hosts/{host_id}", response_model=Host, summary="Actualiza un host")
async def update_host(
    host_id: int, payload: HostUpdate, request: Request
) -> Host:
    """Cambia nombre, notas o habilitación del host.

    Deshabilitar un host **revoca su token en la práctica**: su agente recibe un
    401 en la siguiente llamada. El host local no se puede deshabilitar.
    """
    host = _require_host(host_id)
    if payload.enabled is False and int(host["is_local"]):
        _require_mutable(host)
    changes = payload.model_dump(exclude_unset=True)
    updated = fleet.update_host(host_id, changes)
    if updated is None:  # pragma: no cover - borrado concurrente
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Host no encontrado: {host_id}")
    audit(
        "host.updated",
        actor=_actor(request),
        target=f"host:{host_id}",
        details={"campos": sorted(changes)},
    )
    return Host(**updated)


@router.delete("/hosts/{host_id}", response_model=ActionResponse, summary="Elimina un host")
async def delete_host(host_id: int, request: Request) -> ActionResponse:
    """Elimina el host y **revoca su token** con ello (ya no autentica).

    Se borran también sus comandos y su histórico de métricas: el host deja de
    existir, así que sus datos no significan nada. Si quieres conservar el
    histórico, deshabilítalo en lugar de eliminarlo.
    """
    host = _require_host(host_id)
    _require_mutable(host)
    name = host["name"]
    if not fleet.delete_host(host_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Host no encontrado: {host_id}")
    audit(
        "host.deleted",
        actor=_actor(request),
        target=f"host:{host_id}",
        details={"nombre": name, "token": "revocado"},
    )
    logger.info("Host {} eliminado y su token revocado", host_id)
    return ActionResponse(
        success=True, message=f"Host {name} eliminado y su token revocado."
    )


@router.get(
    "/hosts/{host_id}/commands",
    response_model=CommandListResponse,
    summary="Comandos recientes de un host",
)
async def host_commands(
    host_id: int, limit: int = Query(default=20, ge=1, le=200)
) -> CommandListResponse:
    """Historial de comandos del host (útil para depurar un despliegue)."""
    _require_host(host_id)
    commands = fleet.list_commands(host_id=host_id, limit=limit)
    return CommandListResponse(count=len(commands), commands=commands)


# ============================= Enrolamiento =============================
@router.get(
    "/hosts/enrollment",
    response_model=EnrollmentInfo,
    summary="Token de enrolamiento de los agentes (solo administrador)",
)
async def enrollment_info(request: Request) -> EnrollmentInfo:
    """Devuelve el token con el que un agente nuevo se da de alta en este hub.

    **Es un secreto**: quien lo posea puede registrar un host en la flota. Por
    eso el endpoint exige sesión del panel y la propia documentación de la API
    lo advierte. Si ``AGENT_ENROLL_TOKEN`` está definido en el entorno del hub se
    usa ese; si no, se genera uno persistente la primera vez.
    """
    actor = _actor(request)
    session = request.state.session
    if session.get("auth_kind") == "token" and session.get("scope") != "write":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El token de enrolamiento exige un token de API con ámbito 'write' "
            "o la sesión del panel.",
        )
    token = fleet.enrollment_token()
    if not token:  # pragma: no cover - solo si el directorio no es escribible
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "No se pudo obtener el token de enrolamiento.",
        )
    audit("host.enrollment.read", actor=actor, target="hosts/enrollment")
    return EnrollmentInfo(
        token=token,
        source=fleet.get_settings_source(),
        command=(
            "python3 agent.py enroll --hub https://HUB "
            f"--enroll-token {token} --name NOMBRE"
        ),
        warning=(
            "Este token es un secreto: cualquier persona que lo obtenga puede "
            "dar de alta un host en tu panel. Compártelo solo por un canal de "
            "confianza y rota el token si se filtra."
        ),
    )


# ============================== Histórico ================================
@router.get(
    "/hosts/{host_id}/metrics/history",
    response_model=MetricsHistoryResponse,
    summary="Histórico de métricas de un host",
)
async def host_metrics_history(
    host_id: int,
    range_key: Literal["1h", "24h", "7d", "30d"] = Query(
        default="24h", alias="range", description="Ventana a representar (1h|24h|7d|30d)."
    ),
    metric: str = Query(
        default="cpu,ram", description="Métricas separadas por comas (cpu,ram,swap,disk,load1,temp)."
    ),
) -> MetricsHistoryResponse:
    """Igual que ``/api/metrics-history`` pero acotado a un host de la flota."""
    _require_host(host_id)
    try:
        payload = metrics_store.history(range_key, _parse_metrics(metric), host_id=host_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return MetricsHistoryResponse(**payload)


# ============================== Despacho ================================
@router.post(
    "/hosts/{host_id}/command",
    response_model=CommandDispatchResponse,
    summary="Ejecuta una herramienta en un host y espera el resultado",
)
async def dispatch_command(
    host_id: int, payload: CommandRequest, request: Request
) -> CommandDispatchResponse:
    """Encola un comando para el agente y espera a que lo ejecute.

    Es el despachador genérico de la plataforma: con él, un ``docker.deploy``
    pedido desde el panel se ejecuta **en el host que lo pidió**, y el resultado
    vuelve por el mismo camino que en modo local.

    * 409 si el host está desconectado (no tiene sentido esperar).
    * 504 si el agente no responde en ``timeout_s``; el comando queda ``expired``
      para que se vea que nunca llegó a ejecutarse.
    """
    host = _require_host(host_id)
    actor = _actor(request)
    if not int(host["enabled"]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El host {host['name']} está deshabilitado: rehabilítalo antes de enviarle comandos.",
        )
    if not host["online"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El host {host['name']} está desconectado: no acepta comandos. "
            f"Comprueba que su agente esté en marcha (último latido hace "
            f"{host.get('seconds_since_seen')} s).",
        )

    started = time.monotonic()
    command = fleet.create_command(host_id, payload.tool, payload.args, actor=actor)
    audit(
        "host.command.requested",
        actor=actor,
        target=f"host:{host_id}",
        details={"comando": command["id"], "herramienta": payload.tool},
    )

    deadline = started + payload.timeout_s
    while True:
        stored = fleet.get_command(int(command["id"])) or {}
        state = str(stored.get("status") or "")
        if state in ("done", "error", "expired"):
            return CommandDispatchResponse(
                command_id=int(command["id"]),
                host_id=host_id,
                tool=payload.tool,
                status=state,
                result=stored.get("result"),
                error=stored.get("error"),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fleet.expire_command(int(command["id"]))
            audit(
                "host.command.timeout",
                actor=actor,
                target=f"host:{host_id}",
                result="fail",
                details={"comando": command["id"], "herramienta": payload.tool},
            )
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                f"El agente del host {host['name']} no respondió en {payload.timeout_s} s.",
            )
        await asyncio.sleep(min(DISPATCH_POLL_INTERVAL, remaining))


__all__ = ["DISPATCH_POLL_INTERVAL", "router"]