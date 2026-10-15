"""Configuración de red con watchdog anti-autobloqueo (Fase 6).

Expone el estado de red, la previsualización del cambio de IP, la aplicación
con confirmación obligatoria y la reversión (manual o automática). Toda la
lógica vive en ``app.modules.network_config``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.modules import network_config
from app.schemas.models import (
    NetworkApplyResponse,
    NetworkInterfaceInfo,
    NetworkPlanRequest,
    NetworkPlanResponse,
    NetworkStateResponse,
    NetworkTokenRequest,
    NetworkTokenResponse,
)

router = APIRouter(tags=["red"])


@router.get(
    "/network/state",
    response_model=NetworkStateResponse,
    summary="Estado de red del sistema",
)
async def network_state() -> NetworkStateResponse:
    """Interfaces, direcciones, puerta de enlace y gestor de red detectado."""
    data = network_config.get_state()
    return NetworkStateResponse(
        available=data["available"],
        renderer=data["renderer"],
        supported=data["supported"],
        cloud_init=data["cloud_init"],
        interfaces=[NetworkInterfaceInfo(**item) for item in data["interfaces"]],
        gateway=data.get("gateway"),
        default_interface=data.get("default_interface"),
        dropin=data["dropin"],
        dropin_exists=data["dropin_exists"],
    )


@router.post(
    "/network/plan",
    response_model=NetworkPlanResponse,
    summary="Previsualiza un cambio de IP",
)
async def network_plan(payload: NetworkPlanRequest) -> NetworkPlanResponse:
    """Valida el plan y muestra el fichero netplan que se escribiría."""
    return NetworkPlanResponse(**network_config.plan(payload))


@router.post(
    "/network/apply",
    response_model=NetworkApplyResponse,
    summary="Aplica un cambio de IP (con reversión automática)",
)
async def network_apply(
    payload: NetworkPlanRequest, request: Request
) -> NetworkApplyResponse:
    """Aplica el plan; sin confirmación en el TTL, la red se revierte sola."""
    result = network_config.apply_change(
        payload, request.state.session["username"]
    )
    return NetworkApplyResponse(**result)


@router.post(
    "/network/confirm",
    response_model=NetworkTokenResponse,
    summary="Confirma un cambio de IP pendiente",
)
async def network_confirm(
    payload: NetworkTokenRequest, request: Request
) -> NetworkTokenResponse:
    """Consolida el cambio y cancela la reversión automática."""
    return NetworkTokenResponse(
        **network_config.confirm_change(
            payload.token, request.state.session["username"]
        )
    )


@router.post(
    "/network/revert",
    response_model=NetworkTokenResponse,
    summary="Revierte un cambio de IP",
)
async def network_revert(
    payload: NetworkTokenRequest, request: Request
) -> NetworkTokenResponse:
    """Deshace el cambio (adelanta el watchdog)."""
    return NetworkTokenResponse(
        **network_config.revert_change(
            payload.token, request.state.session["username"]
        )
    )
