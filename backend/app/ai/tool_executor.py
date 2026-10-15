"""Ejecutor de herramientas invocadas por el LLM.

Traduce el nombre de la herramienta y sus argumentos al módulo de dominio
correspondiente. Reglas de seguridad (Fase 5):

* Las herramientas ``proponer_*`` NUNCA mutan nada: crean una propuesta
  pendiente de confirmación humana (``app.ai.pending``).
* **Este ejecutor es el autorizador real** (código, no prompt): valida
  nombres, acciones y servicios contra listas cerradas antes de tocar
  cualquier módulo. Cualquier argumento ``confirm`` que envíe el modelo se
  ignora por diseño.
* Los resultados voluminosos se recortan antes de volver al modelo.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai import pending
from app.core.logging_conf import logger
from app.modules import config_manager, systemd_manager
from app.modules.docker_manager import deploy_container
from app.modules.network_scanner import scan_network
from app.modules.system_monitor import get_system_metrics

#: Tamaño máximo (caracteres) de un resultado antes de recortarlo.
_MAX_RESULT_CHARS = 6000


def _extract_image(arguments: dict[str, Any]) -> str:
    """Normaliza el argumento de imagen (acepta 'imagen', 'image' o 'nombre')."""
    for key in ("imagen", "image", "nombre_imagen", "nombre"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_port(arguments: dict[str, Any]) -> int | None:
    """Normaliza el argumento de puerto (acepta 'puerto' o 'port')."""
    for key in ("puerto", "port"):
        value = arguments.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _service_arg(arguments: dict[str, Any]) -> str:
    """Extrae el nombre de unidad de los argumentos del modelo."""
    for key in ("unidad", "unit", "servicio", "service"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _fit(result: dict[str, Any]) -> dict[str, Any]:
    """Recorta resultados grandes antes de devolverlos al modelo."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result
    if len(text) <= _MAX_RESULT_CHARS:
        return result
    return {
        "success": result.get("success", True),
        "truncated": True,
        "note": "Resultado recortado por tamaño; usa consultas más concretas.",
        "preview": text[:_MAX_RESULT_CHARS],
    }


def _propose(tool: str, args: dict[str, Any], summary: str) -> dict[str, Any]:
    """Crea una propuesta pendiente de confirmación (no ejecuta nada)."""
    action = pending.create(tool, args, summary)
    return {
        "success": True,
        "pending": True,
        "action_id": action["id"],
        "summary": summary,
        "expires_at": action["expires_at"],
        "message": (
            f"Propuesta registrada: {summary}. NO se ha ejecutado nada todavía: "
            "el administrador debe confirmarla en el panel (caduca en 2 minutos)."
        ),
    }


def execute_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ejecuta una herramienta por nombre y devuelve su resultado.

    Args:
        name: Nombre de la herramienta solicitada por el modelo.
        arguments: Argumentos proporcionados por el modelo.

    Returns:
        Diccionario con el resultado de la herramienta. Ante cualquier error,
        incluye la clave ``success=False`` y un campo ``error`` descriptivo.
    """
    # Nota de seguridad: los argumentos llegan del modelo y NO son de fiar;
    # nunca se interpretan como órdenes (p. ej. un 'confirm' inyectado se
    # ignora: la confirmación solo existe en el endpoint autenticado).
    arguments = arguments or {}
    logger.info("El agente solicita la herramienta '{}' con argumentos {}", name, arguments)

    try:
        if name == "escanear_red_local":
            subnet = arguments.get("subnet") or arguments.get("subred") or None
            result = scan_network(subnet)
            result["success"] = result.get("error") is None
            return result

        if name == "desplegar_servicio":
            image = _extract_image(arguments)
            if not image:
                return {
                    "success": False,
                    "error": "No se indicó ninguna imagen para desplegar.",
                }
            return deploy_container(image=image, port=_extract_port(arguments))

        if name == "obtener_estado_sistema":
            result = get_system_metrics()
            result["success"] = True
            return result

        if name == "listar_servicios":
            result = systemd_manager.list_services()
            result["success"] = True
            # Recorte explícito: al modelo no le hacen falta 200 unidades.
            result["services"] = result["services"][:80]
            result["count"] = len(result["services"])
            return _fit(result)

        if name == "estado_servicio":
            unit = _service_arg(arguments)
            try:
                detail = systemd_manager.get_service(unit)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            if detail is None:
                return {
                    "success": False,
                    "error": f"Servicio no encontrado: '{unit}'.",
                }
            detail["success"] = True
            return detail

        if name == "proponer_accion_servicio":
            unit = _service_arg(arguments)
            action = str(arguments.get("accion") or arguments.get("action") or "").strip().lower()
            try:
                normalized = systemd_manager.normalize_unit(unit)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            if action not in ("start", "stop", "restart", "reload"):
                return {"success": False, "error": f"Acción no válida: '{action}'."}
            return _propose(
                "proponer_accion_servicio",
                {"unidad": normalized, "accion": action},
                f"{action} del servicio {normalized}",
            )

        if name == "leer_config_servicio":
            service = str(arguments.get("servicio") or arguments.get("service") or "").strip().lower()
            if service not in ("samba", "dnsmasq"):
                return {
                    "success": False,
                    "error": "Servicio no soportado; usa 'samba' o 'dnsmasq'.",
                }
            data = config_manager.get_config(service)
            return _fit(
                {
                    "success": True,
                    "managed": data["managed"],
                    "path": data["path"],
                    "config": data["config"],
                }
            )

        return {
            "success": False,
            "error": f"Herramienta desconocida: '{name}'.",
        }
    except Exception as exc:  # noqa: BLE001 - el agente nunca debe romper la API
        logger.error("Error ejecutando la herramienta '{}': {}", name, exc)
        return {"success": False, "error": f"Error interno ejecutando '{name}': {exc}"}


__all__ = ["execute_tool"]
