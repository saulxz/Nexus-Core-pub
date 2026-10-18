"""Exportador de métricas en formato de texto de Prometheus (0.0.4).

Publica ``GET /api/metrics/prometheus`` con la misma telemetría que ve el panel,
para que un Prometheus externo pueda rasparla sin conocer el modelo de datos de
Nexus-Core. Familias expuestas:

=============================  ===========================================
Métrica                        Origen
=============================  ===========================================
``nexus_cpu_percent``           Última muestra (``metric_samples``).
``nexus_ram_percent``           Última muestra.
``nexus_swap_percent``          Última muestra.
``nexus_disk_percent``          Última muestra.
``nexus_load1``                 Carga media de 1 min de la última muestra.
``nexus_temperature_celsius``   Última muestra; **se omite** si no hay sensor.
``nexus_uptime_seconds``        ``time.time() - psutil.boot_time()``.
``nexus_monitor_up``            1/0 por monitor habilitado (último heartbeat).
``nexus_monitor_latency_ms``    Latencia del último heartbeat.
``nexus_alerts_open``           Eventos de alerta no cerrados por severidad.
=============================  ===========================================

Decisiones de diseño (formato y semántica):

* Todo es ``gauge``: los valores representan el instante actual, no un contador
  acumulado, así que un reinicio del servicio no genera saltos en la serie.
* Una familia sin muestras **no** se emite (sin ``HELP``/``TYPE`` ni muestras):
  es preferible que la serie aparezca cuando hay sensor a que Prometheus dibuje
  un 0 falso. La excepción es ``nexus_alerts_open``, que siempre publica sus dos
  severidades (``crit`` y ``warn``) para que el panel de alertas tenga una línea
  continua desde el principio.
* Solo se exportan los monitores **habilitados**: un monitor desactivado publicaría
  un estado congelado que confundaría a quien alertara sobre él.
* El nombre del monitor viaja como etiqueta ``monitor``; se escapan ``\\``, las
  comillas y los saltos de línea, tal y como exige el formato de etiquetas.
* Los heartbeats se leen tal cual están: el comprobador ya resume el estado
  (``up`` 1/0) y la latencia del último intento, con lo que un fallo también
  queda representado en la serie.
"""

from __future__ import annotations

import time
from typing import Any

import psutil

from app.core import db
from app.modules import monitors

#: Content-Type del formato de texto de Prometheus.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Severidades que se publican siempre en ``nexus_alerts_open``.
ALERT_SEVERITIES: tuple[str, ...] = ("crit", "warn")


def escape_label(value: str) -> str:
    """Escapa un valor de etiqueta según el formato de texto de Prometheus.

    Solo hay que escapar la barra invertida, la doble comilla y el salto de
    línea: es lo que el parser espera recibir en ``{etiqueta="valor"}``.
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_value(value: float | int) -> str:
    """Formatea un valor de muestra (sin notación exponencial ni ceros de más)."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _sample(name: str, value: float | int, labels: str = "") -> str:
    """Compone una línea de muestra: ``nombre{etiquetas} valor``."""
    suffix = f"{{{labels}}}" if labels else ""
    return f"{name}{suffix} {format_value(value)}"


def _family(lines: list[str], name: str, help_text: str, samples: list[str]) -> None:
    """Añade el bloque ``HELP``/``TYPE`` y sus muestras (si hay alguna)."""
    if not samples:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.extend(samples)


def latest_metric_sample() -> dict[str, Any]:
    """Última muestra de telemetría guardada (vacía si el colector no haCorrido)."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT ts, cpu, ram, swap, disk, load1, temp FROM metric_samples "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row)


def open_alerts_by_severity() -> dict[str, int]:
    """Cuenta los eventos de alerta no cerrados (``open``/``acked``) por severidad."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS total FROM alert_events "
            "WHERE status != 'closed' GROUP BY severity"
        ).fetchall()
    finally:
        conn.close()
    return {str(row["severity"]): int(row["total"]) for row in rows}


def _system_lines(sample: dict[str, Any], now: float) -> list[str]:
    """Métricas del host tomadas de la última muestra y del tiempo de actividad."""
    lines: list[str] = []
    _family(
        lines,
        "nexus_cpu_percent",
        "Uso de CPU del host en porcentaje.",
        [_sample("nexus_cpu_percent", sample["cpu"])] if sample.get("cpu") is not None else [],
    )
    _family(
        lines,
        "nexus_ram_percent",
        "Uso de memoria RAM del host en porcentaje.",
        [_sample("nexus_ram_percent", sample["ram"])] if sample.get("ram") is not None else [],
    )
    _family(
        lines,
        "nexus_swap_percent",
        "Uso de memoria de intercambio en porcentaje.",
        [_sample("nexus_swap_percent", sample["swap"])] if sample.get("swap") is not None else [],
    )
    _family(
        lines,
        "nexus_disk_percent",
        "Uso del disco raíz en porcentaje.",
        [_sample("nexus_disk_percent", sample["disk"])] if sample.get("disk") is not None else [],
    )
    _family(
        lines,
        "nexus_load1",
        "Carga media del sistema a 1 minuto.",
        [_sample("nexus_load1", sample["load1"])] if sample.get("load1") is not None else [],
    )
    # Sin sensor de temperatura no se publica la familia (un 0 sería un error).
    _family(
        lines,
        "nexus_temperature_celsius",
        "Temperatura de la CPU en grados centígrados.",
        (
            [_sample("nexus_temperature_celsius", sample["temp"])]
            if sample.get("temp") is not None
            else []
        ),
    )
    uptime = max(0.0, now - psutil.boot_time())
    _family(
        lines,
        "nexus_uptime_seconds",
        "Segundos desde el arranque del sistema.",
        [_sample("nexus_uptime_seconds", uptime)],
    )
    return lines


def _monitor_lines() -> list[str]:
    """Métricas por monitor habilitado (estado y latencia del último heartbeat)."""
    up_samples: list[str] = []
    latency_samples: list[str] = []
    for monitor in monitors.list_monitors(enabled_only=True):
        beat = monitors.last_heartbeat(int(monitor["id"]))
        if beat is None:
            # Sin comprobaciones todavía: no hay valor real que publicar.
            continue
        labels = f'monitor="{escape_label(str(monitor["name"]))}"'
        up_samples.append(_sample("nexus_monitor_up", 1 if int(beat["up"]) else 0, labels))
        if beat.get("latency_ms") is not None:
            latency_samples.append(
                _sample("nexus_monitor_latency_ms", float(beat["latency_ms"]), labels)
            )
    lines: list[str] = []
    _family(
        lines,
        "nexus_monitor_up",
        "Disponibilidad del último heartbeat de cada monitor (1 = arriba, 0 = abajo).",
        up_samples,
    )
    _family(
        lines,
        "nexus_monitor_latency_ms",
        "Latencia en milisegundos del último heartbeat de cada monitor.",
        latency_samples,
    )
    return lines


def _alert_lines() -> list[str]:
    """Métricas de alertas abiertas, siempre con las dos severidades."""
    counts = open_alerts_by_severity()
    samples = [
        _sample("nexus_alerts_open", counts.get(severity, 0), f'severity="{severity}"')
        for severity in ALERT_SEVERITIES
    ]
    lines: list[str] = []
    _family(
        lines,
        "nexus_alerts_open",
        "Eventos de alerta no cerrados por severidad.",
        samples,
    )
    return lines


def render(now: float | None = None) -> str:
    """Devuelve el documento completo en formato de texto de Prometheus."""
    moment = float(now if now is not None else time.time())
    lines: list[str] = []
    lines.extend(_system_lines(latest_metric_sample(), moment))
    lines.extend(_monitor_lines())
    lines.extend(_alert_lines())
    # La especificación exige terminar el documento con un salto de línea.
    return "\n".join(lines) + "\n"


__all__ = [
    "ALERT_SEVERITIES",
    "CONTENT_TYPE",
    "escape_label",
    "format_value",
    "latest_metric_sample",
    "open_alerts_by_severity",
    "render",
]