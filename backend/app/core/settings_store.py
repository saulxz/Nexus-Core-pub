"""Configuración del agente de IA editable desde la web.

El proveedor LLM, el modelo y las claves de API se guardan en
``$NEXUS_DATA_DIR/config/ai.env`` (permisos 0600, escritura atómica) y se
recargan en caliente: nunca es necesario editar ficheros a mano ni reiniciar
el servicio.

Precedencia para las claves gestionadas aquí: ``ai.env`` manda sobre el
``.env`` del proyecto (la web es la fuente de verdad de la configuración de
IA). El resto de la configuración mantiene el orden normal de
pydantic-settings (variables de entorno reales > ``.env``).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.core.paths import ai_env_path

#: Claves de entorno gestionadas desde la web (las únicas que este módulo toca).
MANAGED_KEYS: tuple[str, ...] = (
    "AI_PROVIDER",
    "AI_MODEL",
    "AI_TEMPERATURE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "OLLAMA_BASE_URL",
)

#: Claves cuyo valor nunca se devuelve en claro al navegador.
SECRET_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
)

PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini", "ollama")

_FIELD_TO_ENV: dict[str, str] = {
    "provider": "AI_PROVIDER",
    "model": "AI_MODEL",
    "temperature": "AI_TEMPERATURE",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "openai_base_url": "OPENAI_BASE_URL",
    "gemini_base_url": "GEMINI_BASE_URL",
    "ollama_base_url": "OLLAMA_BASE_URL",
}

_HEADER = (
    "# Configuracion del agente de IA - generada desde la web de Nexus-Core.\n"
    "# No editar a mano: usa la pagina de Ajustes del panel.\n"
)


# ============================= Fichero ai.env =============================
def _read_file_map() -> dict[str, str]:
    """Lee el mapa de claves gestionadas desde ``ai.env`` (si existe)."""
    path = ai_env_path()
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in MANAGED_KEYS:
            values[key] = value.strip()
    return values


def _write_file_map(values: dict[str, str]) -> None:
    """Escribe ``ai.env`` de forma atómica y con permisos 0600."""
    path = ai_env_path()
    lines = [_HEADER]
    for key in MANAGED_KEYS:
        if key in values:
            lines.append(f"{key}={values[key]}\n")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_ai_env() -> None:
    """Inyecta ``ai.env`` en el entorno y refresca la caché de settings.

    Se invoca al arrancar la aplicación y después de cada guardado desde la
    web, de modo que los cambios tienen efecto inmediato (recarga en caliente).
    """
    values = _read_file_map()
    for key, value in values.items():
        os.environ[key] = value
    get_settings.cache_clear()
    if values:
        logger.info(
            "Configuración de IA cargada desde {} ({} claves)",
            ai_env_path(),
            len(values),
        )


# ================================ Estado ================================
def _mask(value: str) -> str:
    """Representación enmascarada: nunca se expone la clave completa."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * 6 + value[-4:]


def get_ai_state() -> dict[str, Any]:
    """Estado efectivo de la configuración de IA (claves enmascaradas)."""
    settings = get_settings()
    return {
        "provider": settings.AI_PROVIDER.lower(),
        "model": settings.ai_model,
        "temperature": settings.AI_TEMPERATURE,
        "openai_base_url": settings.OPENAI_BASE_URL,
        "gemini_base_url": settings.GEMINI_BASE_URL,
        "ollama_base_url": settings.OLLAMA_BASE_URL,
        "keys": {
            "openai": {
                "configured": bool(settings.OPENAI_API_KEY),
                "masked": _mask(settings.OPENAI_API_KEY),
            },
            "anthropic": {
                "configured": bool(settings.ANTHROPIC_API_KEY),
                "masked": _mask(settings.ANTHROPIC_API_KEY),
            },
            "gemini": {
                "configured": bool(settings.GEMINI_API_KEY),
                "masked": _mask(settings.GEMINI_API_KEY),
            },
        },
    }


def apply_updates(updates: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Aplica los cambios recibidos desde la web y persiste ``ai.env``.

    Reglas:
      * ``None`` (campo ausente) o cadena vacía en secretos = mantener valor.
      * Los secretos nunca se devuelven; solo se anota *qué* cambió.
    """
    values = _read_file_map()
    changed: list[str] = []
    for field, raw in updates.items():
        env_key = _FIELD_TO_ENV.get(field)
        if env_key is None:
            continue
        value = "" if raw is None else str(raw).strip()
        if value == "" and env_key in SECRET_ENV_KEYS:
            continue  # Campo vacío = conservar la clave actual.
        if "\n" in value or "\r" in value:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Valor inválido para '{field}'."
            )
        if field == "provider":
            value = value.lower()
            if value and value not in PROVIDERS:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Proveedor no soportado: '{value}'. Opciones: {', '.join(PROVIDERS)} "
                    "o vacío para desactivar el asistente.",
                )
        elif field == "model":
            if not value:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "El modelo no puede estar vacío."
                )
        elif field == "temperature":
            try:
                numeric = float(value)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Temperatura inválida."
                ) from exc
            if not 0.0 <= numeric <= 2.0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "La temperatura debe estar entre 0 y 2.",
                )
            value = str(numeric)
        elif field.endswith("_base_url"):
            if not value.startswith(("http://", "https://")):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"La URL base de '{field}' debe empezar por http:// o https://.",
                )
        if values.get(env_key) != value:
            changed.append(env_key)
        values[env_key] = value

    if changed:
        _write_file_map(values)
        load_ai_env()
        audit(
            "settings.ai.updated",
            actor=actor,
            target="config/ai.env",
            details={"campos": sorted(changed)},
        )
        logger.info("Ajustes de IA actualizados desde la web: {}", ", ".join(sorted(changed)))
    return get_ai_state()


# ========================= Prueba de conexión =========================
async def test_connection() -> dict[str, Any]:
    """Comprueba la conectividad real con el proveedor LLM configurado.

    Lanza una petición mínima (barata) al proveedor y devuelve
    ``{ok, provider, model, latency_ms, detail}``. Nunca expone la clave.
    """
    settings = get_settings()
    provider = settings.AI_PROVIDER.lower()
    model = settings.ai_model
    started = time.perf_counter()

    provider_key = {
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
    }.get(provider)
    if provider not in PROVIDERS:
        return {
            "ok": False,
            "provider": provider or "sin-configurar",
            "model": model,
            "latency_ms": None,
            "detail": (
                "No hay proveedor seleccionado: el asistente está desactivado "
                "(es opcional). Elige uno —por ejemplo Google Gemini— y guarda."
            ),
        }
    if provider in ("openai", "gemini", "anthropic") and not provider_key:
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "latency_ms": None,
            "detail": f"Falta la clave de API de {provider}. Guárdala antes de probar.",
        }

    async def _request() -> httpx.Response:
        """Una petición mínima al proveedor (conexión nueva en cada intento)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "openai":
                return await client.get(
                    f"{settings.ai_base_url}/models",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                )
            if provider == "gemini":
                return await client.get(
                    f"{settings.ai_base_url}/models",
                    params={"key": settings.GEMINI_API_KEY, "pageSize": 1},
                )
            if provider == "anthropic":
                return await client.post(
                    f"{settings.ai_base_url}/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": model,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            # ollama: la URL configurada es el endpoint compatible OpenAI
            base = settings.ai_base_url
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            return await client.get(f"{base}/api/tags")

    try:
        try:
            response = await _request()
        except httpx.RequestError as first_error:
            # Redes con NAT pueden dejar conexiones muertas: se reintenta una
            # vez con conexión nueva antes de dar el intento por perdido.
            logger.warning(
                "Prueba de IA ({}): primer intento falló ({}); reintentando…",
                provider,
                first_error,
            )
            response = await _request()
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "Prueba de IA ({}) falló tras reintento: {}: {}",
            provider,
            type(exc).__name__,
            exc,
        )
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "latency_ms": elapsed,
            "detail": f"Error de red: {exc}",
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    if response.status_code < 400:
        logger.info("Prueba de IA ({}): conexión correcta ({} ms).", provider, elapsed)
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "latency_ms": elapsed,
            "detail": f"Conexión correcta con {provider} ({response.status_code}).",
        }
    logger.warning(
        "Prueba de IA ({}): HTTP {} — {}",
        provider,
        response.status_code,
        response.text[:200],
    )
    return {
        "ok": False,
        "provider": provider,
        "model": model,
        "latency_ms": elapsed,
        "detail": (
            f"El proveedor respondió {response.status_code}. "
            f"Revisa la clave o el modelo. ({response.text[:200]})"
        ),
    }
