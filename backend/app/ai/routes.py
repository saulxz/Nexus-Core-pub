"""Endpoints REST del Agente de IA.

El chat crea propuestas (nunca ejecuta acciones mutantes por sí mismo); las
propuestas se confirman o cancelan desde endpoints autenticados que aplican
la acción por el mismo camino auditado que el resto del panel.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from app.ai import pending
from app.ai.agent import run_agent
from app.core.audit import audit
from app.core.logging_conf import logger
from app.modules import docker_manager, service_control
from app.schemas.models import (
    ChatRequest,
    ChatResponse,
    PendingActionInfo,
    ToolAction,
)

router = APIRouter(tags=["ia"])

_REASON_TO_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "expired": status.HTTP_409_CONFLICT,
    "state": status.HTTP_409_CONFLICT,
}


def _pending_info(stored: dict) -> PendingActionInfo:
    return PendingActionInfo(
        id=stored["id"],
        tool=stored["tool"],
        summary=stored["summary"],
        created_at=stored["created_at"],
        expires_at=stored["expires_at"],
        status=stored["status"],
    )


def _extract_pending(actions: list[dict]) -> list[PendingActionInfo]:
    """Recoge las propuestas creadas durante una conversación."""
    items: list[PendingActionInfo] = []
    for action in actions:
        result = action.get("result") or {}
        if result.get("pending") and result.get("action_id"):
            stored = pending.get(result["action_id"])
            if stored:
                items.append(_pending_info(stored))
    return items


@router.post("/chat", response_model=ChatResponse, summary="Conversa con el Agente de IA")
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Procesa un mensaje en lenguaje natural y ejecuta acciones si procede.

    El agente decide autónomamente si debe invocar herramientas. Las acciones
    mutantes solo se *proponen*: quedan pendientes de confirmación humana.
    """
    actor = request.state.session["username"]
    pending.set_actor(actor)
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
            pending_actions=_extract_pending(result.get("actions", [])),
            model=result["model"],
            provider=result["provider"],
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - se devuelve un error HTTP controlado
        logger.exception("Fallo en el endpoint /chat: {}", exc)
        raise HTTPException(status_code=500, detail=f"Error del agente de IA: {exc}") from exc


@router.get(
    "/chat/actions",
    response_model=list[PendingActionInfo],
    summary="Propuestas de la IA pendientes",
)
async def list_actions(request: Request) -> list[PendingActionInfo]:
    """Lista las propuestas recientes de la sesión actual."""
    actor = request.state.session["username"]
    return [_pending_info(item) for item in pending.list_for(actor)]


@router.post(
    "/chat/actions/{action_id}/confirm",
    summary="Confirma una propuesta de la IA",
)
async def confirm_action(action_id: str, request: Request) -> dict:
    """Ejecuta una propuesta confirmada por la persona autenticada."""
    actor = request.state.session["username"]
    outcome = pending.confirm(action_id, actor)
    if not outcome["success"]:
        raise HTTPException(
            _REASON_TO_STATUS.get(outcome.get("reason") or "", status.HTTP_409_CONFLICT),
            outcome["message"],
        )

    stored = outcome["action"] or {}
    tool = stored.get("tool")
    args = stored.get("args") or {}
    if tool == "proponer_accion_servicio":
        result = service_control.control(
            str(args.get("unidad", "")),
            str(args.get("accion", "")),
            f"ia-confirmado:{actor}",
        )
    elif tool == "docker_prune_images":
        # Autorremediación propuesta por una regla de alerta (nunca automática).
        result = docker_manager.prune(images=True)
    elif tool == "container_restart":
        restarted = await asyncio.to_thread(
            docker_manager.restart_container, str(args.get("contenedor", ""))
        )
        result = {"success": restarted["success"], "message": restarted["message"]}
    elif tool == "service_restart":
        result = service_control.control(
            str(args.get("unidad", "")),
            str(args.get("accion") or "restart"),
            f"remediacion-confirmada:{actor}",
        )
    else:
        result = {
            "success": False,
            "message": f"Herramienta no confirmable: '{tool}'.",
        }

    audit(
        "ai.action.confirmed",
        actor=actor,
        target=str(args.get("unidad", "")),
        result="ok" if result["success"] else "fail",
        details={
            "tool": tool,
            "accion": args.get("accion", ""),
            "salida": str(result.get("message", ""))[:300],
        },
    )
    return {"success": result["success"], "message": result["message"]}


@router.post(
    "/chat/actions/{action_id}/cancel",
    summary="Cancela una propuesta de la IA",
)
async def cancel_action(action_id: str, request: Request) -> dict:
    """Descarta una propuesta sin ejecutarla."""
    actor = request.state.session["username"]
    outcome = pending.cancel(action_id, actor)
    if not outcome["success"]:
        raise HTTPException(
            _REASON_TO_STATUS.get(outcome.get("reason") or "", status.HTTP_409_CONFLICT),
            outcome["message"],
        )
    audit("ai.action.cancelled", actor=actor, details={"action_id": action_id})
    return {"success": True, "message": outcome["message"]}
