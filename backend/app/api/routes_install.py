"""Catálogo e instalación de servicios apt desde el panel.

Las claves están limitadas por `/etc/nexus/installable.json` y el helper
privilegiado. Cada instalación exige confirmación explícita y queda auditada.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.core import fleet, security
from app.core.audit import audit
from app.modules import installer

router = APIRouter(tags=["instalador"])


class InstallRequest(BaseModel):
    """Confirmación explícita de una instalación."""

    confirm: bool = False



class ManualInstallRequest(InstallRequest):
    """Paquete apt escrito manualmente por el administrador."""

    package: str = ""
    totp: str = ""


@router.get("/install/catalog", summary="Catálogo de servicios instalables")
async def install_catalog() -> dict:
    return await asyncio.to_thread(installer.catalog)


async def _remote_install(host_id: int, package: str, actor: str) -> dict:
    host = fleet.get_host(int(host_id))
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Host no encontrado: {host_id}")
    if int(host.get("is_local") or 0):
        return await asyncio.to_thread(installer.install_manual, package)
    if not int(host.get("enabled") or 0) or not host.get("online"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"El host {host['name']} no est? disponible.")
    version = str(host.get("agent_version") or "0")
    parts = tuple(int(part) for part in version.split(".")[:3] if part.isdigit())
    if parts < (1, 2, 0):
        raise HTTPException(status.HTTP_409_CONFLICT, "Actualiza el agente a 1.2.0 para instalar paquetes.")
    command = fleet.create_command(int(host_id), "install.manual", {"package": package}, actor=actor)
    from app.api.routes_agent import signal_command

    signal_command(int(host_id))
    command_id = int(command["id"])
    deadline = time.monotonic() + 200.0
    while True:
        stored = fleet.get_command(command_id) or {}
        state = str(stored.get("status") or "")
        if state == "done":
            result = stored.get("result")
            if isinstance(result, dict):
                return result
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "El agente devolvi? una respuesta inv?lida.")
        if state in ("error", "expired"):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(stored.get("error") or state))
        if time.monotonic() >= deadline:
            fleet.expire_command(command_id)
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "El agente no respondi? en 200 s.")
        await asyncio.sleep(0.35)


@router.post("/install/manual", summary="Instala un paquete apt manual")
async def install_manual(
    package_request: ManualInstallRequest,
    request: Request,
    host_id: int | None = Query(default=None, ge=1),
) -> dict:
    if not package_request.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta la confirmaci?n expl?cita.")
    package = (package_request.package or "").strip().lower()
    if not package:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Falta el nombre del paquete.")
    if not installer.PACKAGE_RE.fullmatch(package):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nombre de paquete no v?lido.")
    actor = str(request.state.session["username"])
    if bool(security.get_totp_state(actor).get("enabled")):
        if not package_request.totp or not security.verify_second_factor(actor, package_request.totp).get("ok"):
            audit("system.package.install.reauth_failed", actor=actor, target=package)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "C?digo 2FA no v?lido o caducado.")
    audit("system.package.install.requested", actor=actor, target=package)
    result = await _remote_install(host_id, package, actor) if host_id else await asyncio.to_thread(installer.install_manual, package)
    audit("system.package.install", actor=actor, target=package, result="ok" if result.get("success") else "fail", details={"mensaje": str(result.get("message") or result.get("reason") or "")[:300]})
    return result


@router.post("/install/{key}", summary="Instala un servicio del catálogo")
async def install_service(key: str, payload: InstallRequest, request: Request) -> dict:
    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Falta la confirmación explícita para instalar el servicio.",
        )

    actor = str(request.state.session["username"])
    audit("system.package.install.requested", actor=actor, target=key)
    result = await asyncio.to_thread(installer.install, key)
    audit(
        "system.package.install",
        actor=actor,
        target=key,
        result="ok" if result.get("success") else "fail",
        details={"mensaje": str(result.get("message") or result.get("reason") or "")[:300]},
    )
    return result
