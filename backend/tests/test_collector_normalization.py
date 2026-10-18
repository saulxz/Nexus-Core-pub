"""Regresión: el colector debe traducir las claves de la API a la BD.

El fallo original: ``get_system_metrics`` devuelve ``cpu_percent`` /
``ram_percent`` / ``load_avg`` pero ``insert_sample`` y el motor de alertas
leen ``cpu`` / ``ram`` / ``load1``, así que las muestras se guardaban con
todas las columnas a NULL y ninguna regla podía dispararse.
"""

from __future__ import annotations

from app.modules.metrics_collector import _normalize_sample


def test_normalize_api_sample_keys() -> None:
    """Con el dict real de la API, todas las métricas quedan informadas."""
    raw = {
        "cpu_percent": 12.5,
        "cpu_count": 4,
        "ram_percent": 40.0,
        "ram_used_mb": 1024.0,
        "ram_total_mb": 4096.0,
        "swap_percent": 1.0,
        "disk_percent": 55.0,
        "cpu_temp_c": None,
        "uptime_seconds": 100.0,
        "load_avg": [0.4, 0.3, 0.2],
        "timestamp": "2026-10-17T12:00:00+00:00",
    }

    assert _normalize_sample(raw) == {
        "cpu": 12.5,
        "ram": 40.0,
        "swap": 1.0,
        "disk": 55.0,
        "load1": 0.4,
        "temp": None,
    }


def test_normalize_keeps_already_normalized_samples() -> None:
    """Los ``sample_fn`` inyectados que ya usan el esquema nuevo no se tocan."""
    raw = {
        "cpu": 9.0,
        "ram": 20.0,
        "swap": 0.0,
        "disk": 30.0,
        "load1": 0.1,
        "temp": 45.0,
    }

    assert _normalize_sample(raw) == raw
