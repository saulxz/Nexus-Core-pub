"""Punto de entrada de la aplicación FastAPI de Nexus-Core.

Responsabilidades:
* Crear la aplicación y configurar el logging.
* Cargar la configuración de IA editable (``ai.env``), inicializar la base de
  datos de autenticación (primer arranque incluido) y aplicar las migraciones
  del esquema de métricas y alertas.
* Arrancar el colector de métricas en segundo plano (muestreo, agregados y
  motor de alertas) y el comprobador de monitores de disponibilidad, y detener
  ambos limpiamente al apagar.
* Proteger los routers de la API con sesión + CSRF (``require_auth``); los
  tokens de API (``Authorization: Bearer``) son una credencial alternativa con su
  propio ámbito, y el servidor MCP usa ``require_read_auth``. Los **agentes
  remotos** usan su propio token (``app.api.routes_agent.require_agent``) y el
  router del protocolo se registra sin dependencia global.
* Servir el frontend estático (panel) en la raíz ``/``.
* Aplicar cabeceras de seguridad y una limitación de petición básica.
"""

from __future__ import annotations

import asyncio
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

from app.ai import pending
from app.ai.routes import router as ai_router
from app.api.routes import public_router
from app.api.routes import router as core_router
from app.api.routes_agent import router as agent_router
from app.api.routes_alerts import router as alerts_router
from app.api.routes_auth import router as auth_router
from app.api.routes_config import router as config_router
from app.api.routes_hosts import router as hosts_router
from app.api.routes_install import router as install_router
from app.api.routes_mcp import router as mcp_router
from app.api.routes_monitors import public_router as status_public_router
from app.api.routes_monitors import router as monitors_router
from app.api.routes_network import router as network_router
from app.api.routes_security import router as security_router
from app.api.routes_services import router as services_router
from app.api.routes_settings import router as settings_router
from app.api.routes_system import router as system_router
from app.api.routes_terminal import router as terminal_router
from app.core import db, security, settings_store
from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger, setup_logging
from app.modules.metrics_collector import MetricsCollector
from app.modules.monitor_checker import MonitorChecker

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
    schema = db.migrate()
    logger.info("Esquema de métricas y alertas en versión {}", schema)
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
    # Colector de métricas: muestreo cada 5 s, agregados por minuto/hora y
    # evaluación del motor de alertas sobre cada muestra.
    collector = MetricsCollector()
    collector_task = asyncio.create_task(collector.run(), name="nexus-metrics-collector")
    # Comprobador de monitores: heartbeats de http/tcp/ssl y purga del
    # histórico. Relee los monitores habilitados cada vuelta, así que los
    # cambios del panel se aplican sin reiniciar el servicio.
    checker = MonitorChecker()
    checker_task = asyncio.create_task(checker.run(), name="nexus-monitor-checker")
    try:
        yield
    finally:
        await collector.stop()
        await checker.stop()
        for task in (collector_task, checker_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - el cierre nunca debe fallar
                logger.warning("Una tarea de fondo terminó con un error: {}", exc)
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
    alerts_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    monitors_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
app.include_router(
    ai_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# seguridad: tokens de API, sesiones abiertas y segundo factor (TOTP).
app.include_router(
    security_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# herramientas del sistema: SMART, puertos, actualizaciones, diagnóstico ZIP,
# inventario de Docker y FIM.
app.include_router(
    system_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# catálogo e instalación de servicios apt con allowlist y helper privilegiado.
app.include_router(
    install_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# Inventario de la flota y despacho de comandos a los agentes remotos (panel).
app.include_router(
    hosts_router,
    prefix=settings.API_PREFIX,
    dependencies=[Depends(security.require_auth)],
)
# terminal SSH por WebSocket: valida sesión, CSRF y origen dentro del handler.
app.include_router(terminal_router, prefix=settings.API_PREFIX)
# Servidor MCP: se registra SIN dependencia global porque su transporte es un
# POST de solo lectura y aplica su propio control de acceso
# (``require_read_auth``: sesión+CSRF o token de API con ámbito «read»).
app.include_router(mcp_router, prefix=settings.API_PREFIX)
# Protocolo del agente: se registra SIN dependencia global porque NO es la API
# del panel. Cada endpoint trae su propia credencial (``require_agent``, token
# de agente) y el enrolamiento usa el token de enrolamiento del hub. Un agente
# no tiene cookie de sesión ni CSRF.
app.include_router(agent_router, prefix=settings.API_PREFIX)
# health: público (healthcheck de Docker y monitorización).
app.include_router(public_router, prefix=settings.API_PREFIX)
# Estado público, página de estado y exportación a Prometheus: también públicos,
# pero con su propio control de acceso (el de Prometheus exige METRICS_TOKEN
# si está configurado).
app.include_router(status_public_router, prefix=settings.API_PREFIX)


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
