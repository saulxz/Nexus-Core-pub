"""Monitorización de la telemetría del sistema (orientado a Raspberry Pi).

Recoge el uso de CPU, memoria RAM/swap, disco, carga media, tiempo de actividad
y la temperatura de la CPU. La temperatura se obtiene de forma nativa leyendo el
sensor térmico de Linux (``/sys/class/thermal``), con un *fallback* a los
sensores expuestos por ``psutil`` cuando están disponibles.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil

# Ruta del sensor térmico principal en la mayoría de kernels Linux (incluida
# Raspberry Pi OS): valor en milésimas de grado centígrado.
_THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"


def _now_iso() -> str:
    """Marca temporal actual en formato ISO8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _read_cpu_temperature() -> float | None:
    """Lee la temperatura de la CPU en grados centígrados.

    Primero intenta leer el sensor térmico nativo de Linux y, si no existe,
    recurre a ``psutil.sensors_temperatures()``. Devuelve ``None`` si no hay
    ningún sensor accesible (p. ej. en un PC de desarrollo).

    Returns:
        Temperatura en °C redondeada a un decimal, o ``None``.
    """
    # --- 1. Sensor térmico nativo de Linux ---
    try:
        with open(_THERMAL_ZONE_PATH, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
        if raw:
            return round(int(raw) / 1000.0, 1)
    except (FileNotFoundError, ValueError, OSError):
        pass

    # --- 2. Fallback: sensores de psutil ---
    try:
        reader = getattr(psutil, "sensors_temperatures", None)
        if reader is not None:
            temperatures = reader() or {}
            for entries in temperatures.values():
                for entry in entries:
                    if entry.current:
                        return round(float(entry.current), 1)
    except Exception:  # noqa: BLE001 - sensores no soportados en este SO
        pass

    return None


def get_system_metrics() -> dict[str, Any]:
    """Recoge la telemetría completa del sistema.

    Returns:
        Diccionario con el uso de CPU/RAM/swap/disco, temperatura, carga media,
        tiempo de actividad y marca temporal.
    """
    # Se usa un pequeño intervalo para obtener una medida representativa y no
    # una instantánea instantánea sin historial.
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    # Carga media del sistema (no disponible en algunos S.O.).
    try:
        load_avg = [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        load_avg = [0.0, 0.0, 0.0]

    uptime_seconds = max(0.0, time.time() - psutil.boot_time())

    return {
        "cpu_percent": round(float(cpu_percent), 1),
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "ram_percent": round(float(memory.percent), 1),
        "ram_used_mb": round(memory.used / (1024 * 1024), 1),
        "ram_total_mb": round(memory.total / (1024 * 1024), 1),
        "swap_percent": round(float(swap.percent), 1),
        "disk_percent": round(float(disk.percent), 1),
        "cpu_temp_c": _read_cpu_temperature(),
        "uptime_seconds": round(uptime_seconds, 1),
        "load_avg": load_avg,
        "timestamp": _now_iso(),
    }
