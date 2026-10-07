"""Configuración del sistema de logging basado en Loguru.

Se registra un sink a stdout (con color) y, opcionalmente, un sink a fichero
con rotación para su consulta desde el volumen persistente ``/app/data``.
"""

from __future__ import annotations

import sys

from loguru import logger

from app.core.config import get_settings

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging() -> None:
    """Inicializa el logger global de la aplicación (idempotente)."""
    settings = get_settings()

    # Elimina el sink por defecto para evitar mensajes duplicados.
    logger.remove()

    # Sink principal: consola (visible con `docker compose logs`).
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL.upper(),
        format=_LOG_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # Sink secundario: fichero rotativo en el volumen persistente.
    # Se envuelve en try/except porque en desarrollo local puede no existir
    # el directorio /app/data (solo se crea dentro del contenedor).
    try:
        logger.add(
            "/app/data/nexus.log",
            level=settings.LOG_LEVEL.upper(),
            format=_LOG_FORMAT,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            enqueue=True,
        )
    except Exception:  # noqa: BLE001 - el logging no debe impedir el arranque
        logger.debug("No se pudo crear el sink de fichero /app/data/nexus.log")


__all__ = ["logger", "setup_logging"]
