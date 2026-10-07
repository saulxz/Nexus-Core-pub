"""Ejecutor de herramientas invocadas por el LLM.

Traduce el nombre de la herramienta y sus argumentos (en el idioma del esquema
de Function Calling) a llamadas reales a los módulos de dominio del backend.
Toda ejecución está envuelta en un manejo de errores que devuelve siempre un
diccionario estructurado, nunca una excepción.
"""

from __future__ import annotations

from typing import Any

from app.core.logging_conf import logger
from app.modules.docker_manager import deploy_container
from app.modules.network_scanner import scan_network
from app.modules.system_monitor import get_system_metrics


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


def execute_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ejecuta una herramienta por nombre y devuelve su resultado.

    Args:
        name: Nombre de la herramienta solicitada por el modelo.
        arguments: Argumentos proporcionados por el modelo.

    Returns:
        Diccionario con el resultado de la herramienta. Ante cualquier error,
        incluye la clave ``success=False`` y un campo ``error`` descriptivo.
    """
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

        return {
            "success": False,
            "error": f"Herramienta desconocida: '{name}'.",
        }
    except Exception as exc:  # noqa: BLE001 - el agente nunca debe romper la API
        logger.error("Error ejecutando la herramienta '{}': {}", name, exc)
        return {"success": False, "error": f"Error interno ejecutando '{name}': {exc}"}


__all__ = ["execute_tool"]
