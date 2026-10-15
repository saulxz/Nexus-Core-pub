"""Servicios del sistema: inventario (Fase 1) y control (Fase 2).

El inventario es de solo lectura. Las acciones mutantes (start/stop/restart/
reload) pasan por el helper privilegiado ``svc-ctl`` con lista blanca de
unidades y quedan registradas en la auditoría.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.modules import service_control, systemd_manager
from app.schemas.models import (
    ServiceActionRequest,
    ServiceActionResponse,
    ServiceDetailResponse,
    ServiceInfo,
    ServiceListResponse,
    ServiceLogsResponse,
)

router = APIRouter(tags=["servicios"])


@router.get(
    "/services",
    response_model=ServiceListResponse,
    summary="Inventario de servicios del sistema",
)
async def list_services() -> ServiceListResponse:
    """Lista las unidades de servicio (los objetivos del panel, primero)."""
    result = systemd_manager.list_services()
    return ServiceListResponse(
        available=result["available"],
        reason=result.get("reason"),
        count=len(result["services"]),
        services=[ServiceInfo(**item) for item in result["services"]],
    )


@router.get(
    "/services/{name}",
    response_model=ServiceDetailResponse,
    summary="Detalle de un servicio",
)
async def service_detail(name: str) -> ServiceDetailResponse:
    """Devuelve el estado detallado de una unidad concreta."""
    try:
        detail = systemd_manager.get_service(name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if detail is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Servicio no encontrado: {name}"
        )
    return ServiceDetailResponse(**detail)


@router.get(
    "/services/{name}/logs",
    response_model=ServiceLogsResponse,
    summary="Últimos logs de un servicio",
)
async def service_logs(
    name: str, lines: int = Query(default=100, ge=1, le=500)
) -> ServiceLogsResponse:
    """Últimas entradas del journal para la unidad indicada."""
    try:
        result = systemd_manager.get_logs(name, lines=lines)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ServiceLogsResponse(
        available=result["available"],
        reason=result.get("reason"),
        count=len(result["lines"]),
        lines=result["lines"],
    )


@router.post(
    "/services/{name}/action",
    response_model=ServiceActionResponse,
    summary="Controla un servicio (start/stop/restart/reload)",
)
async def service_action(
    name: str, payload: ServiceActionRequest, request: Request
) -> ServiceActionResponse:
    """Ejecuta una acción de control con lista blanca y auditoría."""
    actor = request.state.session["username"]
    result = service_control.control(name, payload.action, actor)
    service = (
        ServiceDetailResponse(**result["service"]) if result.get("service") else None
    )
    return ServiceActionResponse(
        success=result["success"], message=result["message"], service=service
    )
