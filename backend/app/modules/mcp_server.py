"""Servidor MCP (Model Context Protocol) mínimo, sobre JSON-RPC 2.0.

Implementa el núcleo del protocolo sin ninguna dependencia externa
(``json`` + un dispatch de métodos), pensado para que un asistente externo
consulte el estado de la plataforma:

* ``initialize`` — presentación del servidor y versión del protocolo.
* ``tools/list`` — catálogo de herramientas (solo lectura).
* ``tools/call`` — ejecución de una herramienta.

Todas las herramientas son **de solo lectura**: exponen el mismo estado que ya
muestra el panel (estado del sistema, histórico de métricas, monitores, alertas,
contenedores y FIM). Ninguna muta nada, así que un cliente MCP no necesita
ámbito ``write``.

Transporte: ``POST /api/mcp`` con un único objeto JSON-RPC (o un *batch*, que
se responde con una lista). El cuerpo se lee sin modelo de Pydantic para poder
devolver los errores del protocolo (``-32700`` JSON inválido) en lugar de un 422
de FastAPI.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging_conf import logger
from app.modules import alerts, docker_manager, fim, metrics_store, monitors
from app.modules.metrics_store import METRIC_COLUMNS
from app.modules.system_monitor import get_system_metrics

#: Versión del protocolo MCP que anuncia el servidor.
PROTOCOL_VERSION = "2024-11-05"

#: Códigos de error de JSON-RPC 2.0 usados por este servidor.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ============================= Herramientas =============================
def _tool_system_status(_args: dict[str, Any]) -> dict[str, Any]:
    """Telemetría instantánea del sistema (la misma que muestra el panel)."""
    return get_system_metrics()


def _tool_metrics_summary(args: dict[str, Any]) -> dict[str, Any]:
    """Resumen del histórico de métricas (mínimo, máximo y media por métrica)."""
    range_key = str(args.get("range") or "24h")
    if range_key not in metrics_store.RANGE_PERIODS:
        raise ValueError(
            f"Rango no válido: '{range_key}'. Opciones: {', '.join(metrics_store.RANGE_PERIODS)}."
        )
    history = metrics_store.history(range_key, METRIC_COLUMNS)
    summary: dict[str, Any] = {
        "range": range_key,
        "from_ts": history["from_ts"],
        "to_ts": history["to_ts"],
        "metrics": {},
    }
    for metric, points in history["series"].items():
        values = [item["value"] for item in points if item.get("value") is not None]
        if not values:
            summary["metrics"][metric] = {"samples": 0}
            continue
        summary["metrics"][metric] = {
            "samples": len(values),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
        }
    return summary


def _tool_list_monitors(_args: dict[str, Any]) -> dict[str, Any]:
    """Monitores de disponibilidad con su estado actual."""
    items = monitors.list_summaries()
    return {
        "count": len(items),
        "monitors": [
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "status": item["status"],
                "latency_ms": item["latency_ms"],
                "last_check": item["last_check"],
                "uptime_24h": item["uptime_24h"],
                "uptime_7d": item["uptime_7d"],
            }
            for item in items
        ],
    }


def _tool_open_alerts(args: dict[str, Any]) -> dict[str, Any]:
    """Eventos de alerta abiertos (no cerrados) o reconocidos."""
    limit = max(1, min(int(args.get("limit") or 20), 100))
    events = alerts.list_events(status="open", limit=limit) + alerts.list_events(
        status="acked", limit=limit
    )
    return {
        "count": len(events),
        "alerts": [
            {
                "id": item["id"],
                "rule_id": item["rule_id"],
                "metric": item["metric"],
                "status": item["status"],
                "severity": item["severity"],
                "value": item["value"],
                "threshold": item["threshold"],
                "opened_at": item["opened_at"],
                "message": item["message"],
            }
            for item in events
        ],
    }


def _tool_list_containers(_args: dict[str, Any]) -> dict[str, Any]:
    """Contenedores Docker del host con su estado."""
    items = docker_manager.list_containers()
    return {
        "count": len(items),
        "containers": [
            {
                "id": item["short_id"],
                "name": item["name"],
                "image": item["image"],
                "status": item["status"],
                "ports": item["ports"],
            }
            for item in items
        ],
    }


def _tool_fim_status(_args: dict[str, Any]) -> dict[str, Any]:
    """Integridad de los ficheros de configuración vigilados por el FIM."""
    return fim.status()


#: Catálogo de herramientas: ``nombre -> (descripción, esquema, implementación)``.
TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "system_status": (
        "Estado actual del sistema: CPU, RAM, swap, disco, temperatura y uptime.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_system_status,
    ),
    "metrics_summary": (
        "Resumen (mínimo, máximo y media) de las métricas del rango pedido.",
        {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": list(metrics_store.RANGE_PERIODS),
                    "description": "Ventana a resumir (1h, 24h, 7d, 30d).",
                }
            },
            "additionalProperties": False,
        },
        _tool_metrics_summary,
    ),
    "list_monitors": (
        "Monitores de disponibilidad definidos en el panel, con estado y uptime.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_monitors,
    ),
    "open_alerts": (
        "Alertas abiertas o reconocidas, más recientes primero.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100}
            },
            "additionalProperties": False,
        },
        _tool_open_alerts,
    ),
    "list_containers": (
        "Contenedores Docker del host con su imagen, estado y puertos.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_list_containers,
    ),
    "fim_status": (
        "Estado del FIM de los ficheros de configuración del panel.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _tool_fim_status,
    ),
}


# ============================== JSON-RPC ================================
def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Construye una respuesta JSON-RPC de éxito."""
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Construye una respuesta JSON-RPC de error."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _text_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Envuelve un resultado de herramienta en el formato de contenido MCP."""
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
        ],
        "isError": False,
    }


def _initialize(request_id: Any) -> dict[str, Any]:
    """Método ``initialize``: handshake del protocolo y presentación del servidor."""
    settings = get_settings()
    return _result(
        request_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": settings.APP_NAME, "version": settings.APP_VERSION},
            "instructions": (
                "Herramientas de solo lectura sobre el estado de Nexus-Core. "
                "Usa tools/list para descubrirlas y tools/call para consultarlas."
            ),
        },
    )


def _tools_list(request_id: Any) -> dict[str, Any]:
    """Método ``tools/list``: catálogo con el esquema JSON de cada entrada."""
    return _result(
        request_id,
        {
            "tools": [
                {"name": name, "description": description, "inputSchema": schema}
                for name, (description, schema, _handler) in TOOLS.items()
            ]
        },
    )


def _tools_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Método ``tools/call``: ejecuta una herramienta de solo lectura."""
    name = str(params.get("name") or "")
    entry = TOOLS.get(name)
    if entry is None:
        return _error(
            request_id,
            METHOD_NOT_FOUND,
            f"Herramienta desconocida: '{name}'. Disponibles: {', '.join(TOOLS)}.",
        )
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, INVALID_PARAMS, "El campo 'arguments' debe ser un objeto.")
    try:
        return _result(request_id, _text_content(entry[2](arguments)))
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))
    except Exception as exc:  # noqa: BLE001 - el servidor MCP nunca revienta
        logger.warning("La herramienta MCP '{}' falló: {}", name, exc)
        return _error(request_id, INTERNAL_ERROR, f"Error ejecutando '{name}': {exc}")


def handle_message(message: Any) -> dict[str, Any] | None:
    """Atiende un único mensaje JSON-RPC.

    Returns:
        La respuesta, o ``None`` si el mensaje es una *notificación* (no lleva
        ``id`` y, por definición, no se responde).
    """
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Cada mensaje debe ser un objeto JSON.")
    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(method, str) or not method:
        return None if is_notification else _error(request_id, INVALID_REQUEST, "Falta 'method'.")
    if not isinstance(params, dict):
        return None if is_notification else _error(request_id, INVALID_PARAMS, "'params' debe ser un objeto.")

    if method == "initialize":
        return None if is_notification else _initialize(request_id)
    if method in ("notifications/initialized", "initialized", "ping"):
        # Notificación de arranque del cliente: se acusa recibo sin contenido.
        return None if is_notification else _result(request_id, {})
    if method == "tools/list":
        return None if is_notification else _tools_list(request_id)
    if method == "tools/call":
        return None if is_notification else _tools_call(request_id, params)
    return None if is_notification else _error(
        request_id, METHOD_NOT_FOUND, f"Método no soportado: '{method}'."
    )


def handle_payload(payload: Any) -> Any:
    """Atiende el cuerpo de una petición HTTP: un mensaje o un *batch*.

    Returns:
        Un diccionario (respuesta única) o una lista de respuestas (batch). Si
        el batch solo contenía notificaciones, devuelve ``None``.
    """
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "El batch está vacío.")
        responses = [handle_message(item) for item in payload]
        return [item for item in responses if item is not None] or None
    return handle_message(payload)


def handle_raw(body: bytes) -> tuple[dict[str, Any] | list[dict[str, Any]] | None, bool]:
    """Parsea y atiende un cuerpo JSON crudo.

    Returns:
        ``(respuesta, es_error)``. ``es_error`` vale ``True`` para los errores
        de transporte/protocolo, que se devuelven con HTTP 200 y un objeto de
        error JSON-RPC (como exige la especificación).
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return (_error(None, PARSE_ERROR, f"JSON inválido: {exc}"), True)
    response = handle_payload(payload)
    if response is None:
        # Solo notificaciones: no hay cuerpo de respuesta (HTTP 204).
        return (None, False)
    return (response, _is_error_response(response))


def _is_error_response(response: Any) -> bool:
    """True si la respuesta (o alguna del batch) es un objeto de error."""
    items = response if isinstance(response, list) else [response]
    return any(isinstance(item, dict) and "error" in item for item in items)


__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "TOOLS",
    "handle_message",
    "handle_payload",
    "handle_raw",
]