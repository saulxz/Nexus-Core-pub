"""Inventario y estado de servicios del sistema (systemd) — SOLO LECTURA.

Fase 1 del pivote a panel de administración: saber qué servicios hay en la
máquina, cómo están y qué dicen sus últimos logs, **sin ningún privilegio
adicional**. Este módulo no muta nada: únicamente ejecuta consultas.

Medidas de seguridad:
* El nombre de unidad se valida con una expresión regular estricta antes de
  usarse en cualquier comando (defensa frente a inyección de argumentos).
* Los comandos se ejecutan como lista de argumentos, sin shell.
* Todas las llamadas tienen timeout.
* En sistemas sin systemd (p. ej. desarrollo en Windows) el módulo degrada:
  ``available()`` devuelve False y las consultas devuelven un motivo claro.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from app.core.logging_conf import logger

#: Nombre de unidad systemd aceptado (letras, dígitos, ``@ _ . : -``).
_UNIT_RE = re.compile(r"^[A-Za-z0-9@._:\-]{1,128}\.service$")

#: Servicios objetivo del panel (se gestionarán en fases posteriores).
TARGET_SERVICES: dict[str, str] = {
    "smbd.service": "Samba (SMB)",
    "nmbd.service": "Samba (NetBIOS)",
    "smb.service": "Samba (meta Debian)",
    "isc-dhcp-server.service": "Servidor DHCP (ISC)",
    "dnsmasq.service": "DHCP + DNS (dnsmasq)",
    "bind9.service": "DNS (BIND9, Debian)",
    "named.service": "DNS (BIND9)",
}


def available() -> bool:
    """True si systemctl existe en el sistema (Linux con systemd)."""
    return shutil.which("systemctl") is not None


def normalize_unit(name: str) -> str:
    """Normaliza y valida un nombre de unidad.

    Acepta ``smbd`` o ``smbd.service`` y lanza ``ValueError`` si el nombre
    contiene algo que no sea un identificador de unidad legítimo.
    """
    unit = (name or "").strip()
    if unit and not unit.endswith(".service"):
        unit = f"{unit}.service"
    if not _UNIT_RE.match(unit):
        raise ValueError(f"Nombre de unidad no válido: {name!r}")
    return unit


def _run(args: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Ejecuta un comando sin shell y devuelve ``(código, stdout, stderr)``."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Fallo ejecutando '{}': {}", " ".join(args[:2]), exc)
        return -1, "", str(exc)


def list_services() -> dict[str, Any]:
    """Inventario de servicios del sistema (los objetivos, primero)."""
    if not available():
        return {
            "available": False,
            "reason": "systemd no está disponible en este sistema (el inventario funciona en Linux).",
            "services": [],
        }
    code, out, err = _run(
        ["systemctl", "list-units", "--type=service", "--all", "--plain", "--no-legend"]
    )
    if code != 0:
        return {
            "available": False,
            "reason": err.strip() or "systemctl no pudo listar las unidades.",
            "services": [],
        }
    services: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        description = parts[4].strip() if len(parts) > 4 else ""
        services.append(
            {
                "name": unit,
                "friendly_name": TARGET_SERVICES.get(unit),
                "description": description,
                "load_state": load,
                "active_state": active,
                "sub_state": sub,
                "is_target": unit in TARGET_SERVICES,
            }
        )
    services.sort(key=lambda item: (not item["is_target"], item["name"]))
    return {"available": True, "reason": None, "services": services}


def _parse_int(value: str | None) -> int | None:
    """Convierte una propiedad numérica de systemd ('[not set]' -> None)."""
    if value is None:
        return None
    text = value.strip()
    return int(text) if text.isdigit() else None


def get_service(name: str) -> dict[str, Any] | None:
    """Detalle de un servicio vía ``systemctl show`` (None si no existe)."""
    unit = normalize_unit(name)
    if not available():
        return None
    code, out, err = _run(["systemctl", "show", unit, "--no-pager"])
    if code != 0:
        if err.strip():
            logger.warning("systemctl show {} falló: {}", unit, err.strip())
        return None
    props: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key:
            props[key.strip()] = value.strip()
    if not props or props.get("LoadState") == "not-found":
        return None
    return {
        "name": unit,
        "friendly_name": TARGET_SERVICES.get(unit),
        "description": props.get("Description", ""),
        "load_state": props.get("LoadState", ""),
        "active_state": props.get("ActiveState", ""),
        "sub_state": props.get("SubState", ""),
        "unit_file_state": props.get("UnitFileState") or None,
        "main_pid": _parse_int(props.get("MainPID")) or 0,
        "memory_bytes": _parse_int(props.get("MemoryCurrent")),
        "exec_main_status": _parse_int(props.get("ExecMainStatus")) or 0,
        "active_enter_timestamp": props.get("ActiveEnterTimestamp") or None,
        "fragment_path": props.get("FragmentPath") or None,
        "is_target": unit in TARGET_SERVICES,
    }


def get_logs(name: str, lines: int = 100) -> dict[str, Any]:
    """Últimas entradas del journal para un servicio."""
    unit = normalize_unit(name)
    capped = max(1, min(int(lines), 500))
    if not shutil.which("journalctl"):
        return {"available": False, "reason": "journalctl no está disponible.", "lines": []}
    code, out, err = _run(
        ["journalctl", "-u", unit, "-n", str(capped), "--no-pager", "-o", "short-iso"],
        timeout=15.0,
    )
    if code != 0 and not out.strip():
        return {
            "available": False,
            "reason": err.strip() or "No se pudo acceder al journal.",
            "lines": [],
        }
    return {"available": True, "reason": None, "lines": out.splitlines()[-capped:]}
