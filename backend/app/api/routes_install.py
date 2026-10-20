"""Catálogo e instalación de servicios apt desde el panel.

Las claves están limitadas por `/etc/nexus/installable.json` y el helper
privilegiado. Cada instalación exige confirmación explícita y queda auditada.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.audit import audit
from app.modules import installer

router = APIRouter(tags=["instalador"])


class InstallRequest(BaseModel):
    """Confirmación explícita de una instalación."""

    confirm: bool = False


@router.get("/install/catalog", summary="Catálogo de servicios instalables")
async def install_catalog() -> dict:
    return await asyncio.to_thread(installer.catalog)


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
