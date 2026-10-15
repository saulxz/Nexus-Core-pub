"""Configuración guiada de servicios (Fases 3 y 4).

Endpoints para leer, validar (dry-run), aplicar y restaurar la configuración
de Samba y dnsmasq desde el panel, más la sonda previa de DHCP. Todo el
trabajo pesado vive en ``app.modules.config_manager``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.modules import config_manager, dhcp_probe
from app.schemas.models import (
    BackupInfo,
    BackupListResponse,
    ConfigApplyResponse,
    ConfigValidateResponse,
    DhcpCheckResponse,
    DnsmasqConfig,
    DnsmasqConfigResponse,
    DnsmasqConfigUpdate,
    DnsmasqValidateRequest,
    RestoreRequest,
    SambaConfig,
    SambaConfigResponse,
    SambaConfigUpdate,
    SambaValidateRequest,
)

router = APIRouter(tags=["configuracion"])


# ================================ Samba ================================
@router.get(
    "/config/samba",
    response_model=SambaConfigResponse,
    summary="Configuración actual de Samba",
)
async def get_samba_config() -> SambaConfigResponse:
    """Devuelve el modelo de smb.conf (gestionado por el panel o por defecto)."""
    data = config_manager.get_config("samba")
    return SambaConfigResponse(
        managed=data["managed"],
        exists=data["exists"],
        path=data["path"],
        reason=data.get("reason"),
        config=SambaConfig(**(data["config"] or {})),
    )


@router.put(
    "/config/samba",
    response_model=ConfigApplyResponse,
    summary="Aplica la configuración de Samba",
)
async def update_samba_config(
    payload: SambaConfigUpdate, request: Request
) -> ConfigApplyResponse:
    """Renderiza, valida, aplica con backup/rollback y recarga Samba."""
    result = config_manager.save_config(
        "samba", payload.config, request.state.session["username"], payload.force
    )
    return ConfigApplyResponse(**result)


@router.post(
    "/config/samba/validate",
    response_model=ConfigValidateResponse,
    summary="Valida (dry-run) la configuración de Samba",
)
async def validate_samba_config(
    payload: SambaValidateRequest,
) -> ConfigValidateResponse:
    """Genera el borrador y lo valida sin tocar el sistema."""
    return ConfigValidateResponse(
        **config_manager.validate_config("samba", payload.config)
    )


@router.get(
    "/config/samba/backups",
    response_model=BackupListResponse,
    summary="Copias de seguridad de Samba",
)
async def list_samba_backups() -> BackupListResponse:
    """Lista las copias disponibles (últimas 20)."""
    return BackupListResponse(
        backups=[BackupInfo(**item) for item in config_manager.list_backups("samba")]
    )


@router.post(
    "/config/samba/restore",
    response_model=ConfigApplyResponse,
    summary="Restaura una copia de Samba",
)
async def restore_samba_backup(
    payload: RestoreRequest, request: Request
) -> ConfigApplyResponse:
    """Restaura una copia concreta y recarga el servicio."""
    result = config_manager.restore_backup(
        "samba", payload.backup, request.state.session["username"]
    )
    return ConfigApplyResponse(success=result["success"], message=result["message"])


# =============================== Dnsmasq ===============================
@router.get(
    "/config/dnsmasq",
    response_model=DnsmasqConfigResponse,
    summary="Configuración actual de dnsmasq",
)
async def get_dnsmasq_config() -> DnsmasqConfigResponse:
    """Devuelve el modelo del drop-in de dnsmasq (DNS + DHCP)."""
    data = config_manager.get_config("dnsmasq")
    return DnsmasqConfigResponse(
        managed=data["managed"],
        exists=data["exists"],
        path=data["path"],
        reason=data.get("reason"),
        config=DnsmasqConfig(**(data["config"] or {})),
    )


@router.put(
    "/config/dnsmasq",
    response_model=ConfigApplyResponse,
    summary="Aplica la configuración de dnsmasq",
)
async def update_dnsmasq_config(
    payload: DnsmasqConfigUpdate, request: Request
) -> ConfigApplyResponse:
    """Aplica con sonda DHCP previa (si se activa) y rollback automático."""
    result = config_manager.save_config(
        "dnsmasq", payload.config, request.state.session["username"], payload.force
    )
    return ConfigApplyResponse(**result)


@router.post(
    "/config/dnsmasq/validate",
    response_model=ConfigValidateResponse,
    summary="Valida (dry-run) la configuración de dnsmasq",
)
async def validate_dnsmasq_config(
    payload: DnsmasqValidateRequest,
) -> ConfigValidateResponse:
    """Genera el borrador y lo valida sin tocar el sistema."""
    return ConfigValidateResponse(
        **config_manager.validate_config("dnsmasq", payload.config)
    )


@router.get(
    "/config/dnsmasq/backups",
    response_model=BackupListResponse,
    summary="Copias de seguridad de dnsmasq",
)
async def list_dnsmasq_backups() -> BackupListResponse:
    """Lista las copias disponibles (últimas 20)."""
    return BackupListResponse(
        backups=[BackupInfo(**item) for item in config_manager.list_backups("dnsmasq")]
    )


@router.post(
    "/config/dnsmasq/restore",
    response_model=ConfigApplyResponse,
    summary="Restaura una copia de dnsmasq",
)
async def restore_dnsmasq_backup(
    payload: RestoreRequest, request: Request
) -> ConfigApplyResponse:
    """Restaura una copia concreta y recarga el servicio."""
    result = config_manager.restore_backup(
        "dnsmasq", payload.backup, request.state.session["username"]
    )
    return ConfigApplyResponse(success=result["success"], message=result["message"])


@router.post(
    "/config/dnsmasq/dhcp-check",
    response_model=DhcpCheckResponse,
    summary="Sonda de servidores DHCP en la red",
)
async def dhcp_check() -> DhcpCheckResponse:
    """Comprueba si hay otros servidores DHCP respondiendo en la red."""
    return DhcpCheckResponse(**dhcp_probe.probe())
