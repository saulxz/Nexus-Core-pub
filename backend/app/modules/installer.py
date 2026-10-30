"""Puente entre el panel y el instalador apt privilegiado.

El helper vive fuera de la aplicación, se ejecuta como root mediante una regla
sudoers concreta y solo acepta claves del catálogo `/etc/nexus/installable.json`.
La aplicación nunca construye un comando apt con texto del usuario.
"""

from __future__ import annotations

import json
import re
import subprocess

from app.core.proc import run_hidden

HELPER = "/usr/local/lib/nexus/pkg-install"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _run(arguments: list[str], timeout: int = 210) -> dict:
    try:
        result = run_hidden(
            ["sudo", "-n", HELPER, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "available": False,
            "success": False,
            "reason": "El instalador apt solo está disponible en un host Linux con el helper instalado.",
            "catalog": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "success": False,
            "reason": "La instalación agotó el tiempo de espera.",
            "catalog": [],
        }

    output = (result.stdout or "").strip()
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError:
        payload = {"success": False, "message": (result.stderr or output or "Respuesta inválida del helper.")[-500:]}
    if result.returncode != 0 and "success" not in payload:
        payload["success"] = False
    return payload


def catalog() -> dict:
    payload = _run(["catalog"])
    if "catalog" not in payload:
        payload["catalog"] = []
    payload.setdefault("available", True)
    return payload


def install(key: str) -> dict:
    if not KEY_RE.match(key or ""):
        return {"success": False, "message": "Clave de servicio no válida."}
    return _run(["install", key])
