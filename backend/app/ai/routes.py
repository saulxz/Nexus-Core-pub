"""Endpoints REST del Agente de IA.

El prefijo ``/api`` se añade en ``main.py`` al incluir este router, de modo que
la ruta final es ``POST /api/chat``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.ai.agent import run_agent
from app.core.logging_conf import logger
from app.schemas.models import ChatRequest, ChatResponse, ToolAction

router = APIRouter(tags=["ia"])


@router.post("/chat", response_model=ChatResponse, summary="Conversa con el Agente de IA")
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Procesa un mensaje en lenguaje natural y ejecuta acciones si procede.

    El agente decide autónomamente si debe invocar herramientas (escaneo de red,
    despliegue de contenedores o telemetría) para responder con datos reales.
    """
    try:
        history = (
            [message.model_dump() for message in payload.history]
            if payload.history
            else None
        )
        result = await run_agent(payload.message, history)
        return ChatResponse(
            reply=result["reply"],
            actions=[ToolAction(**action) for action in result.get("actions", [])],
            model=result["model"],
            provider=result["provider"],
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - se devuelve un error HTTP controlado
        logger.exception("Fallo en el endpoint /chat: {}", exc)
        raise HTTPException(status_code=500, detail=f"Error del agente de IA: {exc}") from exc
