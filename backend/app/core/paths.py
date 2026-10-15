"""Resolución centralizada de rutas de datos de Nexus-Core.

Todo el estado persistente de la aplicación (base de datos SQLite, fichero
de configuración editable desde la web y registro de auditoría) vive bajo un
único directorio:

* ``NEXUS_DATA_DIR`` si la variable de entorno está definida (systemd la fija
  a ``/var/lib/nexus`` en el despliegue nativo).
* ``/app/data`` cuando la app corre dentro del contenedor Docker (volumen
  con nombre ``nexus_data``).
* ``backend/data`` como último recurso en desarrollo local.

Las rutas se resuelven en cada llamada (no se cachean a nivel de módulo) para
que los tests puedan redefinir ``NEXUS_DATA_DIR`` dinámicamente.
"""

from __future__ import annotations

import os
from pathlib import Path


def _ensure(path: Path, mode: int = 0o700) -> Path:
    """Crea el directorio (y sus padres) con permisos restrictivos."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:  # En Windows los permisos POSIX son un no-op.
        pass
    return path


def data_dir() -> Path:
    """Devuelve (y crea) el directorio raíz de datos."""
    env = os.environ.get("NEXUS_DATA_DIR", "").strip()
    if env:
        return _ensure(Path(env))
    if Path("/app/data").is_dir():
        return _ensure(Path("/app/data"))
    return _ensure(Path(__file__).resolve().parents[2] / "data")


def db_path() -> Path:
    """Ruta de la base de datos SQLite (admin, sesiones, rate limit)."""
    return data_dir() / "nexus.db"


def config_dir() -> Path:
    """Directorio de configuración editable desde la web."""
    return _ensure(data_dir() / "config")


def ai_env_path() -> Path:
    """Fichero de entorno con la configuración del agente de IA (0600)."""
    return config_dir() / "ai.env"


def staging_dir() -> Path:
    """Área de preparación: la web deja aquí los cambios antes de aplicarlos.

    Coincide con ``/var/lib/nexus/staging`` en el despliegue nativo (que es
    la ruta que leen los helpers privilegiados).
    """
    return _ensure(data_dir() / "staging")


def netguard_dir() -> Path:
    """Estado de los cambios de red pendientes de confirmación (watchdog)."""
    return _ensure(data_dir() / "netguard")


def audit_path() -> Path:
    """Fichero JSONL de auditoría encadenada por hash."""
    return data_dir() / "audit.jsonl"


def setup_token_path() -> Path:
    """Fichero con el token de configuración del primer arranque (0600)."""
    return data_dir() / "setup_token"
