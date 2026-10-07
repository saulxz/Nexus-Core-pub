"""Esquema de herramientas (Tools) que el LLM puede invocar.

Se define en el formato estándar de *Function Calling* de OpenAI, que también
es compatible con el endpoint de Ollama. Para Anthropic se transforma
automáticamente al formato ``input_schema`` (ver ``agent.py``).

Cada herramienta corresponde a una capacidad real del backend, de modo que el
modelo actúa como un puente cognitivo entre el lenguaje natural y el sistema
operativo.
"""

from __future__ import annotations

from typing import Any

# ============================ Definición de tools ============================
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "escanear_red_local",
            "description": (
                "Realiza un descubrimiento de hosts activos en la subred local "
                "(ping sweep con nmap). Devuelve las direcciones IP, su estado y, "
                "si es posible, la MAC y el nombre del host. Úsala cuando el "
                "usuario pida auditar, escanear o listar dispositivos de la red."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subnet": {
                        "type": "string",
                        "description": (
                            "Subred a escanear en notación CIDR, por ejemplo "
                            "'192.168.1.0/24'. Solo se permiten rangos privados. "
                            "Si se omite, se usa la subred configurada por defecto."
                        ),
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desplegar_servicio",
            "description": (
                "Despliega un nuevo contenedor Docker a partir de una imagen "
                "autorizada, opcionalmente publicando un puerto en el host. "
                "Úsala cuando el usuario pida levantar, desplegar o instalar un "
                "servicio (por ejemplo un servidor web nginx)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "imagen": {
                        "type": "string",
                        "description": (
                            "Nombre de la imagen Docker a desplegar. Debe "
                            "pertenecer a la lista de imágenes autorizadas "
                            "(alpine, nginx, httpd, busybox, redis, postgres, "
                            "mysql, traefik, portainer)."
                        ),
                    },
                    "puerto": {
                        "type": "integer",
                        "description": (
                            "Puerto del host a publicar para acceder al servicio. "
                            "Opcional."
                        ),
                    },
                },
                "required": ["imagen"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_estado_sistema",
            "description": (
                "Obtiene la telemetría actual del sistema: uso de CPU, memoria "
                "RAM, swap, disco, temperatura de la CPU y tiempo de actividad. "
                "Úsala cuando el usuario pregunte por el estado, la salud o el "
                "rendimiento del dispositivo."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

# ============================== Prompt de sistema ============================
SYSTEM_PROMPT: str = (
    "Eres el Agente de IA integrado en Nexus-Core, una plataforma de "
    "orquestación de infraestructura y ciberseguridad que se ejecuta sobre una "
    "Raspberry Pi con Debian. Tu misión es ayudar al usuario a administrar el "
    "sistema y la red mediante lenguaje natural.\n\n"
    "Dispones de las siguientes herramientas que puedes invocar cuando sea "
    "necesario para responder con datos reales:\n"
    "  - escanear_red_local: audita los dispositivos conectados a la subred.\n"
    "  - desplegar_servicio: levanta un contenedor Docker de una imagen "
    "autorizada.\n"
    "  - obtener_estado_sistema: consulta CPU, RAM, disco y temperatura.\n\n"
    "Reglas de comportamiento:\n"
    "  1. Si el usuario pide una acción o un dato del sistema, invoca la "
    "herramienta adecuada en lugar de inventar la información.\n"
    "  2. No inventes resultados: usa siempre la salida real de las "
    "herramientas.\n"
    "  3. Si una operación no está permitida (por ejemplo, desplegar una imagen "
    "fuera de la lista autorizada), explica al usuario la restricción de "
    "seguridad y sugiere una alternativa válida.\n"
    "  4. Responde siempre en español, de forma clara, breve y profesional.\n"
    "  5. No reveles detalles internos de implementación salvo que se te pidan."
)

__all__ = ["TOOLS", "SYSTEM_PROMPT"]
