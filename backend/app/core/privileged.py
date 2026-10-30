"""Invocación de los helpers privilegiados de Nexus-Core.

Los helpers son scripts propiedad de root (``/usr/local/lib/nexus``) que
realizan las únicas operaciones privilegiadas permitidas: control acotado de
systemd, aplicación de configuración con validación/rollback y cambios de red
con watchdog. Se invocan siempre con ``sudo -n`` (nunca interactivo) y la
autorización vive en ``/etc/sudoers.d/nexus`` + listas controladas por root.

En sistemas sin sudo/systemd (p. ej. desarrollo en Windows) todas las
operaciones devuelven un resultado «no disponible» en lugar de lanzar.
"""

from __future__ import annotations

import shutil
import subprocess

from app.core.proc import run_hidden
from typing import Any

from app.core.logging_conf import logger

HELPERS_DIR = "/usr/local/lib/nexus"


def available() -> bool:
    """True si el sistema puede ejecutar helpers privilegiados (Linux + sudo)."""
    return shutil.which("sudo") is not None and shutil.which("systemctl") is not None


def run_helper(name: str, args: list[str], timeout: float = 45.0) -> dict[str, Any]:
    """Ejecuta ``sudo -n /usr/local/lib/nexus/<name> <args...>``.

    Devuelve ``{"success": bool, "code": int, "output": str}``. El output
    combina stdout y stderr del helper (mensajes en español para la UI).
    """
    if shutil.which("sudo") is None:
        return {
            "success": False,
            "code": -1,
            "output": (
                "El control privilegiado requiere Linux con sudo; "
                "en este sistema no está disponible."
            ),
        }
    command = ["sudo", "-n", f"{HELPERS_DIR}/{name}", *args]
    try:
        completed = run_hidden(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Fallo ejecutando el helper '{}': {}", name, exc)
        return {"success": False, "code": -1, "output": f"Error ejecutando {name}: {exc}"}
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip()
    )
    success = completed.returncode == 0
    if not success:
        logger.warning(
            "Helper '{}' devolvió código {}: {}",
            name,
            completed.returncode,
            output[-300:],
        )
    return {"success": success, "code": completed.returncode, "output": output}
