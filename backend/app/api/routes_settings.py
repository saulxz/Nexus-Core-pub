"""Ajustes de la plataforma editables desde la web.

Fase 0: configuración del agente de IA (proveedor, modelo, claves y URLs
base). Los valores se escriben directamente en ``$NEXUS_DATA_DIR/config/ai.env``
(0600, escritura atómica) y se recargan en caliente: todo se hace desde la
web, sin editar ficheros ni reiniciar el servicio.

El router se registra con la dependencia global de autenticación, por lo que
``request.state.session`` está siempre disponible en los handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core import settings_store
from app.core.audit import audit
from app.schemas.models import (
    AISettingsResponse,
    AISettingsUpdateRequest,
    AITestResponse,
)

router = APIRouter(tags=["ajustes"])


@router.get(
    "/settings/ai",
    response_model=AISettingsResponse,
    summary="Configuración del agente de IA",
)
async def get_ai_settings() -> AISettingsResponse:
    """Devuelve la configuración efectiva del agente (claves enmascaradas)."""
    return AISettingsResponse(**settings_store.get_ai_state())


@router.put(
    "/settings/ai",
    response_model=AISettingsResponse,
    summary="Guarda la configuración del agente de IA",
)
async def update_ai_settings(
    payload: AISettingsUpdateRequest, request: Request
) -> AISettingsResponse:
    """Persiste los cambios en ai.env y recarga la configuración en caliente."""
    actor = request.state.session["username"]
    state = settings_store.apply_updates(
        payload.model_dump(exclude_none=True), actor=actor
    )
    return AISettingsResponse(**state)


@router.post(
    "/settings/ai/test",
    response_model=AITestResponse,
    summary="Prueba la conexión con el proveedor de IA",
)
async def test_ai_connection(request: Request) -> AITestResponse:
    """Lanza una petición mínima al proveedor para validar la clave actual."""
    actor = request.state.session["username"]
    result = await settings_store.test_connection()
    audit(
        "settings.ai.tested",
        actor=actor,
        result="ok" if result["ok"] else "fail",
        details={"proveedor": result.get("provider"), "detalle": result.get("detail")},
    )
    return AITestResponse(**result)
