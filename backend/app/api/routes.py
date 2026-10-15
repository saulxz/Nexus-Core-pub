"""Endpoints REST base de Nexus-Core.

Agrupa los recursos de monitorización del sistema, administración de
contenedores y auditoría de red. El prefijo ``/api`` se aplica en ``main.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.core.logging_conf import logger
from app.modules.docker_manager import deploy_container, list_containers, remove_container
from app.modules.network_scanner import scan_network
from app.modules.system_monitor import get_system_metrics
from app.schemas.models import (
    ContainerInfo,
    DeleteContainerResponse,
    DeployContainerRequest,
    DeployContainerResponse,
    HealthResponse,
    HostInfo,
    MetricsResponse,
    NetworkScanRequest,
    NetworkScanResponse,
)

router = APIRouter(tags=["core"])
# Router público: se registra SIN la dependencia de autenticación.
# Solo contiene /health (healthcheck de Docker y monitorización).
public_router = APIRouter(tags=["core"])


@public_router.get("/health", response_model=HealthResponse, summary="Estado del servicio")
async def health() -> HealthResponse:
    """Comprueba que la API está operativa (usado por el healthcheck de Docker)."""
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.APP_NAME, version=settings.APP_VERSION)


@router.get("/metrics", response_model=MetricsResponse, summary="Telemetría del sistema")
async def metrics() -> MetricsResponse:
    """Devuelve CPU, RAM, swap, disco, temperatura y uptime de la Raspberry Pi."""
    try:
        return MetricsResponse(**get_system_metrics())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fallo obteniendo métricas: {}", exc)
        raise HTTPException(status_code=500, detail=f"Error obteniendo métricas: {exc}") from exc


@router.get("/containers", summary="Lista los contenedores del host")
async def containers() -> dict[str, list[dict]]:
    """Devuelve todos los contenedores del host con su estado y puertos."""
    return {"containers": list_containers()}


@router.post(
    "/containers",
    response_model=DeployContainerResponse,
    summary="Despliega un contenedor",
)
async def create_container(payload: DeployContainerRequest) -> DeployContainerResponse:
    """Despliega un contenedor de una imagen autorizada (con puerto opcional)."""
    result = deploy_container(image=payload.image, port=payload.port, name=payload.name)
    container = ContainerInfo(**result["container"]) if result.get("container") else None
    return DeployContainerResponse(
        success=result["success"],
        message=result["message"],
        container=container,
    )


@router.delete(
    "/containers/{container_id}",
    response_model=DeleteContainerResponse,
    summary="Destruye un contenedor",
)
async def delete_container(container_id: str) -> DeleteContainerResponse:
    """Detiene y elimina un contenedor por su ID o nombre."""
    result = remove_container(container_id)
    return DeleteContainerResponse(success=result["success"], message=result["message"])


@router.post(
    "/network-scan",
    response_model=NetworkScanResponse,
    summary="Escanea la red local",
)
async def network_scan(payload: NetworkScanRequest) -> NetworkScanResponse:
    """Realiza un descubrimiento de hosts activos en una subred privada."""
    result = scan_network(payload.subnet)
    return NetworkScanResponse(
        subnet=result["subnet"],
        hosts=[HostInfo(**host) for host in result["hosts"]],
        count=result["count"],
        scanned_at=result["scanned_at"],
        error=result.get("error"),
    )
