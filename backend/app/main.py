"""Punto de entrada de la aplicación FastAPI de Nexus-Core.

Responsabilidades:
* Crear la aplicación y configurar el logging.
* Cargar la configuración de IA editable (``ai.env``) e inicializar la base
  de datos de autenticación (primer arranque incluido).
* Proteger los routers de la API con sesión + CSRF (``require_auth``).
* Servir el frontend estático (panel) en la raíz ``/``.
* Aplicar cabeceras de seguridad y una limitación de peticiones básica.
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.ai import pending
from app.ai.routes import router as ai_router
from app.api.routes import public_router
from app.api.routes import router as core_router
from app.api.routes_auth import router as auth_router
from app.api.routes_config import router as config_router
from app.api.routes_network import router as network_router
from app.api.routes_services import router as services_router
from app.api.routes_settings import router as settings_router
from app.core import security, settings_store
from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger, setup_logging

settings = get_settings()

# Se configura el logging en el momento de importar el módulo, de forma que
# también quede activo cuando la app se lanza por CLI (uvicorn app.main:app)
# y no únicamente a través del bloque __main__.
setup_logging()


# ======================= Middleware de rate limiting =======================
class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """Limitador de peticiones en memoria por IP para un prefijo de ruta.

    Es suficiente para el alcance del TFG (una sola instancia) y evita añadir
    dependencias externas. En producción se sustituiría por un limitador
    distribuido (Redis + SlowAPI).
    """

    def __init__(
        self,
        app: Any,
        path_prefix: str = "/api/chat",
        max_requests: int = 30,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self.path_prefix = path_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Any:
        if not request.url.path.startswith(self.path_prefix):
            return await call_next(request)

        client_ip = request.client.host if request.client else "desconocido"
        now = time.monotonic()
        bucket = self._hits.setdefault(client_ip, deque())

        # Se descartan las marcas de tiempo fuera de la ventana temporal.
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()

        if len(bucket) >= self.max_requests:
            logger.warning("Rate limit alcanzado para la IP {}", client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Demasiadas peticiones al asistente. "
                        f"Espera hasta {self.window_seconds} segundos."
                    )
                },
            )

        bucket.append(now)
        return await call_next(request)


# ================ Middleware de cabeceras de seguridad ================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Añade cabeceras de seguridad básicas a todas las respuestas."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # El panel se sirve como ficheros estáticos: se fuerza la revalidación
        # para que una actualización del código se vea con un simple recargar.
        if not request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


# ============================ Ciclo de vida ============================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Acciones de arranque y parada de la aplicación."""
    logger.info("Iniciando {} v{}", settings.APP_NAME, settings.APP_VERSION)
    security.init_db()
    pending.init_db()
    settings_store.load_ai_env()
    active = get_settings()
    logger.info(
        "Proveedor de IA: {} ({})",
        active.AI_PROVIDER or "sin configurar (opcional)",
        active.ai_model or "-",
    )
    setup_token = security.ensure_setup_token()
    if setup_token:
        logger.warning(
            "PRIMER ARRANQUE: no hay administrador configurado. Abre el panel "
            "y completa el asistente con este token: {}",
            setup_token,
        )
    audit("app.started", details={"version": settings.APP_VERSION})
    yield
    audit("app.stopped")
    logger.info("Deteniendo {}...", settings.APP_NAME)


# ============================ Aplicación ============================
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Plataforma Web de Orquestación de Infraestructura y Ciberseguridad "
        "con Agente de IA Integrado en entorno Edge (Raspberry Pi)."
    ),
    lifespan=lifespan,
)

# --- CORS ---
# Modo recomendado: sin CORS (panel y API en el mismo origen). Solo se añade
# el middleware si hay orígenes explícitos configurados.
_origins = settings.allowed_origins_list
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif settings.ALLOWED_ORIGINS.strip() == "*":
    logger.warning(
        "ALLOWED_ORIGINS='*' ignorado por seguridad: el panel funciona "
        "same-origin. Configura orígenes explícitos solo si los necesitas."
    )

# --- Hosts permitidos (TrustedHostMiddleware) ---
if settings.allowed_hosts_list != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list
    )

# --- Cabeceras de seguridad ---
app.add_middleware(SecurityHeadersMiddleware)

# --- Rate limiting sobre /api/chat ---
app.add_middleware(SimpleRateLimitMiddleware)

# --- Routers de la API (prefijo /api) ---
# auth: sin dependencia global (login/setup accesibles sin sesión).
app.include_router(auth_router, prefix=settings.API_PREFIX)
# El resto de la API exige sesión válida (+ CSRF en métodos mutantes).
app.include_router(
    settings_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    services_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    config_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    network_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    core_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    ai_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# health: público (healthcheck de Docker y monitorización).
app.include_router(public_router, prefix=settings.API_PREFIX)


# --- Frontend estático (dashboard) ---
_frontend_path = Path(settings.FRONTEND_DIR)
if _frontend_path.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_path), html=True), name="frontend")
    logger.info("Frontend servido desde {}", _frontend_path.resolve())
else:
    logger.warning(
        "Directorio de frontend '%s' no encontrado; la raíz servirá un mensaje simple.",
        settings.FRONTEND_DIR,
    )

    @app.get("/", include_in_schema=False)
    async def root() -> PlainTextResponse:
        return PlainTextResponse(
            f"{settings.APP_NAME} v{settings.APP_VERSION} — API en {settings.API_PREFIX}. "
            "El frontend no está instalado."
        )


if __name__ == "__main__":
    import uvicorn

    setup_logging()
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
