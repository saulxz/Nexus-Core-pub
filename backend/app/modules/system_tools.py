"""Herramientas de diagnóstico del sistema anfitrión (S.M.A.R.T., puertos, apt).

Pensadas para una Raspberry Pi con Debian/Raspberry Pi OS, pero **degradan con
seducción**: si la herramienta no existe o el sistema operativo no es el
esperado, el endpoint responde ``{"available": false, "reason": "..."}`` en
lugar de fallar. Así el mismo panel se developa en Windows sin que las
funciones appearing como averías.

Todas las funciones son **síncronas y bloqueantes** (``subprocess``/``psutil``):
los endpoints las envuelven en ``asyncio.to_thread`` para no bloquear el bucle
de eventos del servidor.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from typing import Any

import psutil

from app.core.logging_conf import logger

#: Tiempo máximo de cada comando externo (s).
COMMAND_TIMEOUT = 20.0

#: Regex de una línea de ``smartctl --scan-open`` (``/dev/sda -d scsi # ...``).
_SCAN_LINE_RE = re.compile(r"^(?P<device>/\S+)(?:\s+-d\s+\S+)?\s*(?:#.*)?$")

#: Regex del resultado de salud en la salida de texto de smartctl.
_HEALTH_RE = re.compile(
    r"SMART overall-health self-assessment test result:\s*(?P<health>[\w-]+)", re.I
)

#: Regex de la temperatura en la salida de texto (``194 Temperature_Celsius ...``).
_TEXT_TEMP_RE = re.compile(
    r"^\s*\d+\s+Temperature_Celsius\s+(?P<value>-?\d+)", re.I | re.M
)

#: Regex del desgaste porcentage en la salida de texto (SSD).
_TEXT_WEAR_RE = re.compile(
    r"Percentage_Lifetime_Used\s*:\s*(?P<value>\d+)%", re.I
)

#: Regex de sectores reasignados en la salida de texto.
_TEXT_REALLOC_RE = re.compile(
    r"Reallocated_Sector_Ct\s*:\s*(?P<value>\d+)", re.I
)


# ============================== Utilidades =============================
def run_capture(command: list[str], timeout: float = COMMAND_TIMEOUT) -> tuple[int, str]:
    """Ejecuta un comando externo y devuelve ``(código, stdout+stderr)``.

    Devuelve ``(-1, "...")`` si el binario no existe o no se puede lanzar: el
    llamador lo traduce a ``available=False`` con el motivo.
    """
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return (-1, f"No se encontró el comando '{command[0]}'.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (-1, f"No se pudo ejecutar '{command[0]}': {exc}")
    return (completed.returncode, (completed.stdout or "") + (completed.stderr or ""))


def _number(value: Any) -> float | None:
    """Convierte a float tolerando los prefijos que usa smartctl en JSON."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ================================= S.M.A.R.T. ============================
def _scan_devices() -> list[str] | None:
    """Lista los discos abiertos con ``smartctl --scan-open``.

    Returns:
        Lista de dispositivos, o ``None`` si el comando no está disponible o
        falló (con el motivo ya registrado en el log).
    """
    if shutil.which("smartctl") is None:
        return None
    code, output = run_capture(["smartctl", "--scan-open"])
    if code != 0:
        logger.warning("smartctl --scan-open devolvió {}: {}", code, output[-200:])
        return None
    devices: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SCAN_LINE_RE.match(line)
        if match:
            devices.append(match.group("device"))
    return devices


def _smart_from_json(device: str) -> dict[str, Any] | None:
    """Extrae los datos de salud de la salida JSON de ``smartctl -j``.

    Los atributos se buscan por identificador y no por posición: cada fabricante
    los ordena de forma distinta (``temperature`` puede ser el 194 o el 190).
    """
    code, output = run_capture(["smartctl", "-H", "-A", "-i", "-j", device])
    if code not in (0, 2) or not output.strip():
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    health = None
    status = payload.get("smart_status") or {}
    if isinstance(status, dict):
        health = status.get("passed")

    temperature: float | None = None
    wear: float | None = None
    reallocated: int | None = None
    table = payload.get("ata_smart_attributes") or {}
    entries = table.get("table") if isinstance(table, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).lower()
        raw_value = (entry.get("raw") or {}).get("value")
        if name in ("temperature_celsius", "current_drive_temperature"):
            temperature = _number(raw_value)
        elif name in ("percentage_lifetime_used", "media_wear_percentage"):
            wear = _number(raw_value)
        elif name == "reallocated_sector_count":
            reallocated = int(_number(raw_value) or 0)

    model = ""
    model_block = payload.get("model_family") or payload.get("model_name") or ""
    if isinstance(model_block, str):
        model = model_block.strip()

    return {
        "device": device,
        "model": model,
        "health": ("" if health is None else ("PASSED" if health else "FAILED")) or None,
        "temperature": temperature,
        "wear": wear,
        "reallocated_sectors": reallocated,
        "source": "json",
    }


def _smart_from_text(device: str) -> dict[str, Any] | None:
    """Extrae los datos de salud parseando la salida de texto de smartctl.

    Es el *fallback* para versiones antiguas de smartmontools sin salida JSON.
    """
    code, output = run_capture(["smartctl", "-H", "-A", "-i", device])
    if code not in (0, 2) or not output.strip():
        return None
    model = ""
    for raw in output.splitlines():
        line = raw.strip()
        if line.lower().startswith(("device model:", "model family:", "model number:")):
            model = line.split(":", 1)[1].strip()
            break
    health_match = _HEALTH_RE.search(output)
    temp_match = _TEXT_TEMP_RE.search(output)
    wear_match = _TEXT_WEAR_RE.search(output)
    realloc_match = _TEXT_REALLOC_RE.search(output)
    return {
        "device": device,
        "model": model,
        "health": health_match.group("health") if health_match else None,
        "temperature": float(temp_match.group("value")) if temp_match else None,
        "wear": float(wear_match.group("value")) if wear_match else None,
        "reallocated_sectors": (
            int(realloc_match.group("value")) if realloc_match else None
        ),
        "source": "text",
    }


def smart_disks() -> dict[str, Any]:
    """Informe S.M.A.R.T. de los discos del sistema.

    Returns:
        ``{"available": bool, "reason": str | None, "count": int, "disks": [...]}``.
        ``available=False`` si ``smartctl`` no está instalado o el escaneo no
        devuelve discos.
    """
    if shutil.which("smartctl") is None:
        return {
            "available": False,
            "reason": (
                "smartctl no está instalado en este sistema. En Debian/Raspberry "
                "Pi OS: sudo apt-get install smartmontools."
            ),
            "count": 0,
            "disks": [],
        }

    devices = _scan_devices()
    if devices is None:
        return {
            "available": False,
            "reason": (
                "No se pudo ejecutar 'smartctl --scan-open'. Puede hacer falta "
                "permisos de root o que no haya discos compatibles con SMART."
            ),
            "count": 0,
            "disks": [],
        }
    if not devices:
        return {
            "available": False,
            "reason": "smartctl no ha detectado ningún disco con SMART disponible.",
            "count": 0,
            "disks": [],
        }

    disks: list[dict[str, Any]] = []
    for device in devices:
        info = _smart_from_json(device) or _smart_from_text(device)
        if info is not None:
            disks.append(info)
    return {
        "available": True,
        "reason": None,
        "count": len(disks),
        "disks": disks,
    }


# ============================== Puertos =================================
def listening_ports() -> dict[str, Any]:
    """Sockets en escucha con su proceso propietario.

    Requiere privilegios para ver los sockets de otros usuarios: sin ellos,
    ``psutil`` lanza ``AccessDenied`` y se devuelven los sockets legibles
    marcando la respuesta como ``partial``.

    Returns:
        ``{"available", "reason", "partial", "count", "ports"}``.
    """
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError) as exc:
        return {
            "available": False,
            "reason": (
                "Sin permisos para leer la tabla de sockets "
                f"({exc}). Ejecuta el panel como root para verlos todos."
            ),
            "partial": True,
            "count": 0,
            "ports": [],
        }
    except (OSError, NotImplementedError) as exc:  # SO sin /proc/net
        return {
            "available": False,
            "reason": f"La tabla de sockets no está disponible en este sistema: {exc}",
            "partial": False,
            "count": 0,
            "ports": [],
        }

    ports: list[dict[str, Any]] = []
    partial = False
    for connection in connections:
        status = str(connection.status or "")
        if status != "LISTEN" or connection.laddr is None:
            continue
        family = "inet6" if connection.family == socket.AF_INET6 else "inet"
        pid = connection.pid
        name: str | None = None
        if pid:
            try:
                name = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                # Sin permisos (o proceso ya terminado): el PID sigue siendo útil.
                partial = True
        ports.append(
            {
                "family": family,
                "ip": str(connection.laddr.ip),
                "port": int(connection.laddr.port),
                "status": status,
                "pid": pid,
                "process": name,
            }
        )
    ports.sort(key=lambda item: (item["family"], item["ip"], item["port"]))
    return {
        "available": True,
        "reason": None,
        "partial": partial,
        "count": len(ports),
        "ports": ports,
    }


# ========================== Actualizaciones =============================
#: ``Inst <paquete> [<versión> (<arquitectura>)] ...`` de ``apt-get -s upgrade``.
_APT_INST_RE = re.compile(
    r"^Inst\s+(?P<package>\S+)(?:\s+(?P<version>\S+)(?:\s+\((?P<arch>[^)]+)\))?)?"
)


def apt_updates() -> dict[str, Any]:
    """Lista de paquetes actualizables según ``apt-get -s upgrade``.

    Es una **simulación** (``-s``): no instala nada y no necesita ser root
    (``Debug::NoLocking=1`` evita chocar con el apt de otro proceso).

    Returns:
        ``{"available", "reason", "count", "packages"}``.
    """
    if shutil.which("apt-get") is None:
        return {
            "available": False,
            "reason": (
                "apt-get no está disponible en este sistema (solo Debian/Raspberry "
                "Pi OS). En otra distribución no se pueden consultar las actualizaciones."
            ),
            "count": 0,
            "packages": [],
        }

    code, output = run_capture(
        ["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"], timeout=45.0
    )
    if code != 0:
        return {
            "available": False,
            "reason": f"apt-get devolvió el código {code}: {output.strip()[-200:]}",
            "count": 0,
            "packages": [],
        }

    packages: list[dict[str, Any]] = []
    for raw in output.splitlines():
        match = _APT_INST_RE.match(raw.strip())
        if match:
            packages.append(
                {
                    "package": match.group("package"),
                    "version": match.group("version") or "",
                }
            )
    return {"available": True, "reason": None, "count": len(packages), "packages": packages}


__all__ = ["COMMAND_TIMEOUT", "apt_updates", "listening_ports", "run_capture", "smart_disks"]
