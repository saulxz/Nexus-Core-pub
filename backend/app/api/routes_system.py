"""API de herramientas del sistema: S.M.A.R.T., puertos, actualizaciones, Docker y FIM.

Todas las llamadas a herramientas externas (``smartctl``, ``apt-get``, Docker,
``psutil``) son **bloqueantes**: los endpoints las envuelven en
``asyncio.to_thread`` para que el bucle de eventos de uvicorn no se bloquee
mientras el Raspberry Pi habla con el disco o con el daemon.

Cada herramienta **degrada con educación**: si el comando no existe (el panel se
developa en Windows) o faltan permisos, la respuesta es
``{"available": false, "reason": "..."}`` con HTTP 200. Un 500 aquí
significaría «el panel está roto», y no es el caso.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.audit import audit
from app.core.logging_conf import logger
from app.modules import diagnostics, docker_manager, fim, system_tools
from app.schemas.models import (
    ActionResponse,
    ContainerLogsResponse,
    ContainerStatsResponse,
    DockerImage,
    DockerImageListResponse,
    DockerNetwork,
    DockerNetworkListResponse,
    DockerVolume,
    DockerVolumeListResponse,
    FimBaselineRequest,
    FimBaselineResponse,
    FimStatusResponse,
    PruneRequest,
    PortsResponse,
    SmartResponse,
    UpdatesResponse,
)

router = APIRouter(tags=["sistema"])


def _actor(request: Request) -> str:
    """Usuario autenticado (la dependencia global del router ya lo validó)."""
    return str(request.state.session["username"])


# ============================== S.M.A.R.T. ==============================
@router.get("/system/smart", response_model=SmartResponse, summary="Salud de los discos (SMART)")
async def system_smart() -> SmartResponse:
    """Informe SMART de los discos, vía ``smartctl``.

    Usa la salida JSON cuando la versión de smartmontools la soporta y, si no,
    parsea el texto. Sin ``smartctl`` instalado responde ``available: false``.
    """
    return SmartResponse(**await asyncio.to_thread(system_tools.smart_disks))


# ================================ Puertos ===============================
@router.get("/system/ports", response_model=PortsResponse, summary="Puertos en escucha")
async def system_ports() -> PortsResponse:
    """Sockets TCP/UDP en escucha con su PID y proceso (si hay permisos)."""
    return PortsResponse(**await asyncio.to_thread(system_tools.listening_ports))


# =========================== Actualizaciones ============================
@router.get("/system/updates", response_model=UpdatesResponse, summary="Actualizaciones pendientes")
async def system_updates() -> UpdatesResponse:
    """Simulación de ``apt-get upgrade``: qué paquetes se podrían actualizar."""
    return UpdatesResponse(**await asyncio.to_thread(system_tools.apt_updates))


# ============================ Diagnóstico ZIP ===========================
@router.get(
    "/system/diagnostics",
    summary="Descarga el paquete de diagnóstico (ZIP)",
    response_class=StreamingResponse,
)
async def system_diagnostics(request: Request) -> StreamingResponse:
    """Empaqueta logs, configuración, métricas, auditoría y estado en un ZIP.

    Todos los secretos (claves de IA, contraseñas SMTP, tokens de Telegram,
    secretos de sesión, hashes de tokens) se redactan antes de escribir, y el
    manifiesto marca el paquete como **sensible**.
    """
    payload = await asyncio.to_thread(diagnostics.build_bundle)
    diagnostics.record_download(_actor(request), len(payload))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="nexus-diagnostico-{stamp}.zip"'
        },
    )


# =============================== Docker =================================
@router.get(
    "/docker/volumes",
    response_model=DockerVolumeListResponse,
    summary="Lista los volúmenes Docker",
)
async def docker_volumes() -> DockerVolumeListResponse:
    """Volúmenes Docker con su driver y punto de montaje."""
    result = await asyncio.to_thread(docker_manager.list_volumes)
    volumes = [DockerVolume(**item) for item in result["items"]]
    return DockerVolumeListResponse(
        available=result["available"],
        reason=result["reason"],
        count=len(volumes),
        volumes=volumes,
    )


@router.get(
    "/docker/networks",
    response_model=DockerNetworkListResponse,
    summary="Lista las redes Docker",
)
async def docker_networks() -> DockerNetworkListResponse:
    """Redes Docker con sus contenedores conectados."""
    result = await asyncio.to_thread(docker_manager.list_networks)
    networks = [DockerNetwork(**item) for item in result["items"]]
    return DockerNetworkListResponse(
        available=result["available"],
        reason=result["reason"],
        count=len(networks),
        networks=networks,
    )


@router.get(
    "/docker/images",
    response_model=DockerImageListResponse,
    summary="Lista las imágenes Docker",
)
async def docker_images() -> DockerImageListResponse:
    """Imágenes locales con sus tags y tamaño."""
    result = await asyncio.to_thread(docker_manager.list_images)
    images = [DockerImage(**item) for item in result["items"]]
    return DockerImageListResponse(
        available=result["available"],
        reason=result["reason"],
        count=len(images),
        images=images,
    )


@router.get(
    "/containers/{container_id}/logs",
    response_model=ContainerLogsResponse,
    summary="Log de un contenedor",
)
async def container_logs(
    container_id: str,
    tail: int = Query(default=200, ge=1, le=5000, description="Líneas a devolver."),
) -> ContainerLogsResponse:
    """Últimas líneas del log de un contenedor (máx. 5000)."""
    result = await asyncio.to_thread(docker_manager.container_logs, container_id, tail)
    return ContainerLogsResponse(**result)


@router.get(
    "/containers/{container_id}/stats",
    response_model=ContainerStatsResponse,
    summary="Consumo puntual de un contenedor",
)
async def container_stats(container_id: str) -> ContainerStatsResponse:
    """Muestra única de CPU y memoria (el panel la consulta bajo demanda)."""
    result = await asyncio.to_thread(docker_manager.container_stats, container_id)
    return ContainerStatsResponse(**result)


@router.post("/docker/prune", response_model=ActionResponse, summary="Limpia recursos Docker")
async def docker_prune(payload: PruneRequest, request: Request) -> ActionResponse:
    """Elimina imágenes/volúmenes/contenedores sin uso.

    **Destructivo**: sin ``confirm: true`` responde 400 y no hace nada. La
    ejecución queda anotada en la auditoría.
    """
    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Esta operación borra datos de forma irreversible: "
            "vuelve a enviarla con confirm=true.",
        )
    result = await asyncio.to_thread(
        docker_manager.prune,
        payload.images,
        payload.volumes,
        payload.containers,
    )
    audit(
        "docker.prune",
        actor=_actor(request),
        target="docker",
        result="ok" if result["success"] else "fail",
        details={
            "imagenes": payload.images,
            "volumenes": payload.volumes,
            "contenedores": payload.containers,
            "liberado_bytes": result.get("freed_bytes", 0),
            "salida": str(result.get("message", ""))[:300],
        },
    )
    return ActionResponse(success=result["success"], message=result["message"])


# ================================= FIM ==================================
@router.get("/fim/status", response_model=FimStatusResponse, summary="Estado del FIM")
async def fim_status() -> FimStatusResponse:
    """Compara los ficheros gestionados por el panel con su línea base.

    Solo vigila los ficheros de la lista blanca (:data:`app.modules.fim.WATCHED_FILES`):
    el endpoint no acepta ninguna ruta del cliente.
    """
    result = await asyncio.to_thread(fim.status)
    return FimStatusResponse(**result)


@router.post(
    "/fim/baseline",
    response_model=FimBaselineResponse,
    summary="Actualiza la línea base del FIM",
)
async def fim_baseline(payload: FimBaselineRequest, request: Request) -> FimBaselineResponse:
    """Acepta el contenido actual como nueva línea base.

    Solo hay que hacerlo cuando el cambio es legítimo (el propio panel acaba de
    escribir el fichero): aceptarlo a ciegas borra la evidencia de una
    intrusión.
    """
    result = await asyncio.to_thread(
        fim.snapshot, payload.paths, actor=_actor(request)
    )
    logger.info("Línea base del FIM actualizada: {}", ", ".join(result["paths"]) or "-")
    return FimBaselineResponse(**result)


__all__ = ["router"]