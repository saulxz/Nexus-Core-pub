"""Endpoint JSON-RPC 2.0 del servidor MCP (Model Context Protocol).

Transporte: un ``POST`` con un único mensaje JSON-RPC (o un *batch*), tal y como
define el protocolo. Se registra **sin** la dependencia global de la API
privada: la autorización la hace dentro ``require_read_auth``.

Control de acceso (no es público):

* **Token de API** ``Authorization: Bearer nx_...`` — un token de ámbito
  ``read`` basta (las herramientas MCP son de solo lectura). No necesita CSRF,
  como cualquier credencial que no dependa de una cookie.
* **Sesión del panel** — funciona igual, pero al ser un ``POST`` **sí** exige la
  cabecera ``X-CSRF-Token``: si no lo exigiera, este endpoint sería un agujero
  de CSRF (cualquier web podría hacer que el navegador del panel envíe consultas
  al servidor MCP).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.core import security
from app.core.logging_conf import logger
from app.modules import mcp_server

router = APIRouter(tags=["mcp"])


@router.post("/mcp", summary="Servidor MCP (JSON-RPC 2.0)")
async def mcp_endpoint(
    request: Request, _auth: dict = Depends(security.require_read_auth)
) -> Response:
    """Atiende una petición JSON-RPC 2.0 (``initialize``, ``tools/list``, ``tools/call``).

    Los errores de protocolo (JSON inválido, método desconocido...) se devuelven
    como objetos de error JSON-RPC con HTTP 200, según la especificación; los
    errores de autenticación los produce la dependencia, con 401/403.
    """
    body = await request.body()
    payload, is_error = mcp_server.handle_raw(body)
    if payload is None:
        # Solo notificaciones: JSON-RPC no define cuerpo de respuesta.
        return Response(status_code=204)
    logger.debug("Petición MCP atendida (error={})", is_error)
    return JSONResponse(payload)


__all__ = ["router"]