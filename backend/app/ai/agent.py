"""Agente de IA con *Function Calling*.

Implementa el bucle cognitivo que conecta el lenguaje natural del usuario con
las capacidades reales del sistema operativo. El flujo es:

1. Se construye la conversación (historial + mensaje actual) junto al prompt de
   sistema y el catálogo de herramientas.
2. Se envía al proveedor LLM configurado (OpenAI, Anthropic u Ollama).
3. Si el modelo solicita una herramienta, se ejecuta de forma segura en el
   backend y se le devuelve el resultado.
4. El bucle se repite hasta que el modelo emite una respuesta final o se agota
   el número máximo de iteraciones (``AI_MAX_TOOL_ITERATIONS``).

La comunicación con OpenAI/Ollama se hace vía HTTP con ``httpx`` contra el
endpoint ``/chat/completions``; con Anthropic contra ``/v1/messages``; y con
Google Gemini contra ``/models/{model}:generateContent``. Cada proveedor tiene su
propio formato de Function Calling, por lo que el módulo incluye funciones de
traducción (por ejemplo ``_gemini_tools``) que adaptan el catálogo común de
herramientas definido en ``tools_schema.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.ai.tool_executor import execute_tool
from app.ai.tools_schema import SYSTEM_PROMPT, TOOLS
from app.core.config import Settings, get_settings
from app.core.logging_conf import logger


# ============================ Utilidades internas ============================

def _normalize_gemini_model(model: str) -> str:
    """Normaliza el identificador del modelo para la API nativa de Gemini.

    Acepta valores escritos por una persona («gemini 2.5 flash»), con el
    prefijo ``models/`` o en mayúsculas, y devuelve el identificador que
    espera ``/models/{model}:generateContent``.
    """
    value = (model or "").strip()
    if value.lower().startswith("models/"):
        value = value[7:]
    value = re.sub(r"[\s_]+", "-", value).strip("-").lower()
    return value or "gemini-2.5-flash"

def _missing_key_reply(settings: Settings, variable: str) -> dict[str, Any]:
    """Respuesta de cortesía cuando falta la clave del proveedor LLM activo."""
    hint = ""
    provider = settings.AI_PROVIDER.lower()
    if settings.GEMINI_API_KEY and provider != "gemini":
        hint = (
            " Ya tienes una clave de Google Gemini guardada: en «Proveedor» "
            "selecciona «Google Gemini» y pulsa Guardar."
        )
    elif settings.OPENAI_API_KEY and provider != "openai":
        hint = (
            " Ya tienes una clave de OpenAI guardada: en «Proveedor» "
            "selecciona «OpenAI» y pulsa Guardar."
        )
    elif settings.ANTHROPIC_API_KEY and provider != "anthropic":
        hint = (
            " Ya tienes una clave de Anthropic guardada: en «Proveedor» "
            "selecciona «Anthropic» y pulsa Guardar."
        )
    reply = (
        "Ahora mismo no puedo responder porque el proveedor activo "
        f"('{provider}') todavía no tiene su clave de API. "
        "El asistente es opcional: el resto del panel funciona igual sin él. "
        "Para activarlo, abre «Ajustes» en la cabecera, elige el proveedor, "
        "pega su clave y guarda." + hint + f" (Detalle técnico: falta {variable}.)"
    )
    return {
        "reply": reply,
        "actions": [],
        "model": settings.ai_model,
        "provider": provider,
    }


def _build_messages(
    history: list[dict[str, Any]] | None,
    user_message: str,
) -> list[dict[str, Any]]:
    """Construye la lista de mensajes a partir del historial y el mensaje actual."""
    messages: list[dict[str, Any]] = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _anthropic_tools() -> list[dict[str, Any]]:
    """Transforma el catálogo OpenAI de herramientas al formato de Anthropic."""
    tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        function = tool.get("function", {})
        tools.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Convierte en diccionario los argumentos que devuelve el modelo."""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Argumentos de herramienta no parseables: {}", raw_arguments)
    return {}


# ======================= Proveedor OpenAI-compatible =======================
async def _run_openai_compatible(
    settings: Settings,
    user_message: str,
    history: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Bucle de function calling para OpenAI y Ollama."""
    provider = settings.AI_PROVIDER.lower()
    api_key = settings.OPENAI_API_KEY

    if provider == "openai" and not api_key:
        return _missing_key_reply(settings, "OPENAI_API_KEY")

    url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_build_messages(history, user_message),
    ]
    actions: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=settings.AI_REQUEST_TIMEOUT) as client:
            for _ in range(settings.AI_MAX_TOOL_ITERATIONS):
                payload: dict[str, Any] = {
                    "model": settings.ai_model,
                    "temperature": settings.AI_TEMPERATURE,
                    "messages": messages,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                }
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message", {}) or {}
                tool_calls = message.get("tool_calls") or []

                # Sin herramientas solicitadas -> respuesta final.
                if not tool_calls:
                    return {
                        "reply": (message.get("content") or "").strip() or "Sin respuesta.",
                        "actions": actions,
                        "model": settings.ai_model,
                        "provider": provider,
                    }

                # Se añade el mensaje del asistente con las llamadas solicitadas.
                messages.append(message)

                # Se ejecuta cada herramienta y se devuelve el resultado al modelo.
                for call in tool_calls:
                    function = call.get("function", {}) or {}
                    name = function.get("name", "")
                    arguments = _parse_arguments(function.get("arguments"))
                    result = execute_tool(name, arguments)
                    actions.append({"tool": name, "arguments": arguments, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )

            # Se agotaron las iteraciones: se pide una síntesis final sin tools.
            final_payload = {
                "model": settings.ai_model,
                "temperature": settings.AI_TEMPERATURE,
                "messages": messages,
            }
            response = await client.post(url, json=final_payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            message = ((data.get("choices") or [{}])[0]).get("message", {}) or {}
            return {
                "reply": (message.get("content") or "").strip()
                or "Se alcanzó el límite de operaciones; revisa los resultados.",
                "actions": actions,
                "model": settings.ai_model,
                "provider": provider,
            }

    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        logger.error("El proveedor LLM devolvió un error HTTP: {}", detail)
        return {
            "reply": f"El proveedor de IA devolvió un error ({exc.response.status_code}). {detail}",
            "actions": actions,
            "model": settings.ai_model,
            "provider": provider,
        }
    except httpx.RequestError as exc:
        logger.error("No se pudo contactar con el proveedor LLM: {}", exc)
        return {
            "reply": (
                "No se pudo contactar con el proveedor de IA. Verifica la "
                f"conectividad y la URL configurada ({settings.ai_base_url})."
            ),
            "actions": actions,
            "model": settings.ai_model,
            "provider": provider,
        }
    except Exception as exc:  # noqa: BLE001 - el agente no debe tumbar la API
        logger.exception("Error inesperado en el agente: {}", exc)
        return {
            "reply": f"Se produjo un error inesperado al procesar tu petición: {exc}",
            "actions": actions,
            "model": settings.ai_model,
            "provider": provider,
        }


# ============================ Proveedor Anthropic ============================
async def _run_anthropic(
    settings: Settings,
    user_message: str,
    history: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Bucle de function calling para Anthropic (Claude)."""
    if not settings.ANTHROPIC_API_KEY:
        return _missing_key_reply(settings, "ANTHROPIC_API_KEY")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    messages: list[dict[str, Any]] = _build_messages(history, user_message)
    tools = _anthropic_tools()
    actions: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=settings.AI_REQUEST_TIMEOUT) as client:
            for _ in range(settings.AI_MAX_TOOL_ITERATIONS):
                payload = {
                    "model": settings.ai_model,
                    "max_tokens": 1024,
                    "temperature": settings.AI_TEMPERATURE,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                    "tools": tools,
                }
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                content_blocks = data.get("content", []) or []

                text = "".join(
                    block.get("text", "")
                    for block in content_blocks
                    if block.get("type") == "text"
                )
                tool_uses = [block for block in content_blocks if block.get("type") == "tool_use"]

                if not tool_uses:
                    return {
                        "reply": text.strip() or "Sin respuesta.",
                        "actions": actions,
                        "model": settings.ai_model,
                        "provider": "anthropic",
                    }

                # Se reenvía el turno del asistente tal cual lo emitió Claude.
                messages.append({"role": "assistant", "content": content_blocks})

                tool_results: list[dict[str, Any]] = []
                for block in tool_uses:
                    name = block.get("name", "")
                    arguments = block.get("input", {}) or {}
                    result = execute_tool(name, arguments)
                    actions.append({"tool": name, "arguments": arguments, "result": result})
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("id", ""),
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})

            return {
                "reply": "Se alcanzó el límite de operaciones; revisa los resultados.",
                "actions": actions,
                "model": settings.ai_model,
                "provider": "anthropic",
            }

    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        logger.error("Anthropic devolvió un error HTTP: {}", detail)
        return {
            "reply": f"El proveedor Anthropic devolvió un error ({exc.response.status_code}). {detail}",
            "actions": actions,
            "model": settings.ai_model,
            "provider": "anthropic",
        }
    except httpx.RequestError as exc:
        logger.error("No se pudo contactar con Anthropic: {}", exc)
        return {
            "reply": "No se pudo contactar con el proveedor Anthropic. Verifica la conectividad.",
            "actions": actions,
            "model": settings.ai_model,
            "provider": "anthropic",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error inesperado en el agente (Anthropic): {}", exc)
        return {
            "reply": f"Se produjo un error inesperado al procesar tu petición: {exc}",
            "actions": actions,
            "model": settings.ai_model,
            "provider": "anthropic",
        }


# ========================= Proveedor Google Gemini =========================
# Gemini NO usa el formato OpenAI: exige `functionDeclarations` y un esquema
# JSON Schema con los tipos EN MAYÚSCULAS (p. ej. "STRING"), además de roles
# "user"/"model". Las funciones siguientes realizan esa traducción.
_GEMINI_TYPE_MAP: dict[str, str] = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _to_gemini_schema(node: dict[str, Any]) -> dict[str, Any]:
    """Traduce un fragmento de JSON Schema (minúsculas) al formato de Gemini."""
    converted: dict[str, Any] = {}

    raw_type = node.get("type")
    if isinstance(raw_type, str):
        converted["type"] = _GEMINI_TYPE_MAP.get(raw_type.lower(), "STRING")
    if "description" in node:
        converted["description"] = node["description"]
    if "enum" in node:
        converted["enum"] = node["enum"]

    properties = node.get("properties")
    if properties:
        converted["properties"] = {
            key: _to_gemini_schema(value) for key, value in properties.items()
        }

    required = node.get("required")
    if required:
        converted["required"] = required

    items = node.get("items")
    if items:
        converted["items"] = _to_gemini_schema(items)

    return converted


def _gemini_tools() -> list[dict[str, Any]]:
    """Convierte el catálogo OpenAI de herramientas al formato de Gemini."""
    declarations: list[dict[str, Any]] = []
    for tool in TOOLS:
        function = tool.get("function", {})
        parameters = function.get("parameters", {"type": "object", "properties": {}})
        declarations.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "parameters": _to_gemini_schema(parameters),
            }
        )
    return [{"functionDeclarations": declarations}]


def _gemini_contents(
    history: list[dict[str, Any]] | None,
    user_message: str,
) -> list[dict[str, Any]]:
    """Construye la lista `contents` de Gemini (roles `user` y `model`)."""
    contents: list[dict[str, Any]] = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if not content:
            continue
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            # Gemini llama "model" al turno del asistente.
            contents.append({"role": "model", "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    return contents


async def _run_gemini(
    settings: Settings,
    user_message: str,
    history: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Bucle de function calling para Google Gemini."""
    if not settings.GEMINI_API_KEY:
        return _missing_key_reply(settings, "GEMINI_API_KEY")

    model = _normalize_gemini_model(settings.ai_model)
    url = f"{settings.GEMINI_BASE_URL.rstrip('/')}/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.GEMINI_API_KEY,
    }
    contents = _gemini_contents(history, user_message)
    actions: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=settings.AI_REQUEST_TIMEOUT) as client:
            for _ in range(settings.AI_MAX_TOOL_ITERATIONS):
                payload: dict[str, Any] = {
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": contents,
                    "tools": _gemini_tools(),
                    "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                    "generationConfig": {"temperature": settings.AI_TEMPERATURE},
                }
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

                candidate = (data.get("candidates") or [{}])[0]
                parts = ((candidate.get("content") or {}).get("parts")) or []
                function_calls = [
                    part["functionCall"] for part in parts if part.get("functionCall")
                ]
                text = "".join(part.get("text", "") for part in parts if part.get("text"))

                # Sin llamadas a funciones -> respuesta final.
                if not function_calls:
                    return {
                        "reply": text.strip() or "Sin respuesta.",
                        "actions": actions,
                        "model": model,
                        "provider": "gemini",
                    }

                # Se reenvía el turno del modelo tal cual (contiene las llamadas).
                contents.append({"role": "model", "parts": parts})

                # Se ejecuta cada herramienta y se devuelve el resultado.
                function_responses: list[dict[str, Any]] = []
                for call in function_calls:
                    name = call.get("name", "")
                    arguments = call.get("args", {}) or {}
                    result = execute_tool(name, arguments)
                    actions.append({"tool": name, "arguments": arguments, "result": result})
                    function_responses.append(
                        {"functionResponse": {"name": name, "response": result}}
                    )
                contents.append({"role": "user", "parts": function_responses})

            # Iteraciones agotadas: síntesis final sin herramientas.
            final_payload: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {"temperature": settings.AI_TEMPERATURE},
            }
            response = await client.post(url, json=final_payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            candidate = (data.get("candidates") or [{}])[0]
            parts = ((candidate.get("content") or {}).get("parts")) or []
            text = "".join(part.get("text", "") for part in parts if part.get("text"))
            return {
                "reply": text.strip()
                or "Se alcanzó el límite de operaciones; revisa los resultados.",
                "actions": actions,
                "model": model,
                "provider": "gemini",
            }

    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        logger.error("Gemini devolvió un error HTTP: {}", detail)
        return {
            "reply": f"Gemini devolvió un error ({exc.response.status_code}). {detail}",
            "actions": actions,
            "model": model,
            "provider": "gemini",
        }
    except httpx.RequestError as exc:
        logger.error("No se pudo contactar con Gemini: {}", exc)
        return {
            "reply": "No se pudo contactar con la API de Gemini. Verifica la conectividad.",
            "actions": actions,
            "model": model,
            "provider": "gemini",
        }
    except Exception as exc:  # noqa: BLE001 - el agente no debe tumbar la API
        logger.exception("Error inesperado en el agente (Gemini): {}", exc)
        return {
            "reply": f"Se produjo un error inesperado al procesar tu petición: {exc}",
            "actions": actions,
            "model": model,
            "provider": "gemini",
        }


# ================================ Punto de entrada ==========================
async def run_agent(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ejecuta el agente de IA para un mensaje del usuario.

    Args:
        user_message: Mensaje del usuario en lenguaje natural.
        history: Historial previo de la conversación, como lista de
            ``{"role": ..., "content": ...}``.

    Returns:
        Diccionario con ``reply``, ``actions``, ``model`` y ``provider``.
    """
    settings = get_settings()
    provider = settings.AI_PROVIDER.lower()
    logger.info("run_agent -> proveedor='{}', modelo='{}'", provider, settings.ai_model)

    if provider == "anthropic":
        return await _run_anthropic(settings, user_message, history)
    if provider == "gemini":
        return await _run_gemini(settings, user_message, history)
    if provider in ("openai", "ollama"):
        return await _run_openai_compatible(settings, user_message, history)

    return {
        "reply": (
            "El asistente de IA no está configurado ahora mismo. No es "
            "obligatorio: el resto del panel (servicios, red, contenedores) "
            "funciona igual sin él. Si quieres activarlo, abre «Ajustes» en la "
            "cabecera, elige un proveedor (por ejemplo, Google Gemini), pega su "
            "clave y guarda."
        ),
        "actions": [],
        "model": settings.ai_model,
        "provider": provider or "sin-configurar",
    }


__all__ = ["run_agent"]
