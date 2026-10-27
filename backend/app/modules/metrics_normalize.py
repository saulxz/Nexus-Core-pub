"""Normalización de muestras de métricas compartida por el hub y el agente.

Este módulo **no** importa nada del hub (FastAPI, SQLite, argon2, alertas):
el agente remoto solo necesita traducir el dict de la API al esquema de la
base de datos, sin arrastrar el stack completo del panel.
"""
from __future__ import annotations

from typing import Any


def normalize_sample(raw: dict[str, Any]) -> dict[str, Any]:
    """Traduce el dict de la API al esquema de la base de datos.

    ``get_system_metrics`` devuelve nombres de la API (``cpu_percent``,
    ``ram_percent``…) mientras que ``metric_samples`` y el motor de alertas
    trabajan con ``cpu``, ``ram``, ``swap``, ``disk``, ``load1`` y ``temp``.
    Se aceptan ambas formas (la normalizada gana) para no romper los
    ``sample_fn`` inyectados en las pruebas.
    """
    loads = raw.get("load_avg") or []
    load1 = raw.get("load1")
    if load1 is None and loads:
        load1 = loads[0]
    return {
        "cpu": raw.get("cpu", raw.get("cpu_percent")),
        "ram": raw.get("ram", raw.get("ram_percent")),
        "swap": raw.get("swap", raw.get("swap_percent")),
        "disk": raw.get("disk", raw.get("disk_percent")),
        "load1": load1,
        "temp": raw.get("temp", raw.get("cpu_temp_c")),
    }


__all__ = ["normalize_sample"]
