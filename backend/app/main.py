"""Punto de entrada de la aplicación FastAPI de Nexus-Core.

Responsabilidades:
* Crear la aplicación y configurar el logging.
* Habilitar CORS.
* Registrar los routers de la API (core e IA) bajo el prefijo ``/api``.
* Servir el frontend estático (dashboard) en la raíz ``/``.
* Aplicar una limitación de peticiones básica sobre ``/api/chat``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.ai.routes import router as ai_router
from app.api.routes import router as core_router
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


# ============================ Ciclo de vida ============================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Acciones de arranque y parada de la aplicación."""
    logger.info("Iniciando {} v{}", settings.APP_NAME, settings.APP_VERSION)
    logger.info("Proveedor de IA: {} ({})", settings.AI_PROVIDER, settings.AI_MODEL)
    yield
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Rate limiting sobre /api/chat ---
app.add_middleware(SimpleRateLimitMiddleware)

# --- Routers de la API (prefijo /api) ---
app.include_router(core_router, prefix=settings.API_PREFIX)
app.include_router(ai_router, prefix=settings.API_PREFIX)


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
