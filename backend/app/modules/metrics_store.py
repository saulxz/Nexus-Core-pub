"""Persistencia y consulta del histórico de métricas (muestras y agregados).

Modelo de almacenamiento (jerarquía de resoluciones, ver ``app.core.db``):

* ``metric_samples``: una fila cada 5 s. Retención 48 h.
* ``metric_rollups`` con periodo ``1m``: media de cada minuto. Retención 30 d.
* ``metric_rollups`` con periodo ``5m``: media de 5 minutos. Retención 180 d.
* ``metric_rollups`` con periodo ``1h``: media horaria. Retención 2 años.

Cada nivel se calcula a partir del inferior (1m desde muestras, 5m desde 1m y
1h desde 5m), de forma que una Raspberry Pi con SD solo necesita guardar 48 h
de datos crudos y conserva el histórico anual con muy poco espacio.

Multi-host: todas las lecturas y escrituras están particionadas por ``host_id``
(migración 5). El valor por defecto es el host local (id 1), que es el que
produce el colector de esta máquina, de modo que el modo local no cambia.

Este módulo es puramente de datos: no conoce el motor de alertas ni la API.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.db import LOCAL_HOST_ID
from app.core.logging_conf import logger

#: Métricas almacenadas (columnas de ``metric_samples`` y ``metric_rollups``).
METRIC_COLUMNS: tuple[str, ...] = ("cpu", "ram", "swap", "disk", "load1", "temp")

#: Periodos de agregación, del más reciente al más antiguo.
ROLLUP_PERIODS: tuple[str, ...] = ("1m", "5m", "1h")

#: Duración en segundos de cada periodo.
PERIOD_SECONDS: dict[str, int] = {"1m": 60, "5m": 300, "1h": 3600}

#: Nivel del que se calcula cada agregado (1m se calcula de las muestras crudas).
PARENT_PERIOD: dict[str, str] = {"5m": "1m", "1h": "5m"}

#: Retención por nivel (segundos). El purgado se ejecuta cada hora.
RETENTION_SECONDS: dict[str, int] = {
    "raw": 48 * 3600,          # 48 horas
    "1m": 30 * 24 * 3600,      # 30 días
    "5m": 180 * 24 * 3600,     # 180 días
    "1h": 2 * 365 * 24 * 3600, # 2 años
}

#: Rango de la API → nivel de la pirámide usado para responder.
#: 1 h y 24 h se sirven con ``1m`` (720/1440 puntos), 7 días con ``5m`` (2016)
#: y 30 días con ``1h`` (720): en todos los casos por debajo del tope de 1000
#: puntos que acepta el frontend sin remuestrear.
RANGE_PERIODS: dict[str, str] = {
    "1h": "1m",
    "24h": "1m",
    "7d": "5m",
    "30d": "1h",
}

#: Segundos que cubre cada rango admitido.
RANGE_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}

#: Número máximo de puntos devueltos por serie en el histórico.
MAX_POINTS = 1000

#: Último bucket reconstruido por (host, periodo). Evita reconstruir el mismo
#: bucket en cada heartbeat: el cálculo es idempotente, pero repetirlo cada 5 s
#: en la SD de una Raspberry Pi es trabajo y escritura innecesarios.
_built_buckets: dict[tuple[int, str], int] = {}


# ============================== Muestras ==============================
def insert_sample(ts: int, sample: dict[str, Any], host_id: int = LOCAL_HOST_ID) -> None:
    """Guarda una muestra en bruto (idempotente para el mismo host y ``ts``)."""
    values = [
        float(sample[name]) if sample.get(name) is not None else None
        for name in METRIC_COLUMNS
    ]
    placeholders = ", ".join("?" for _ in METRIC_COLUMNS)
    columns = ", ".join(METRIC_COLUMNS)
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO metric_samples (host_id, ts, {columns}) "
            f"VALUES (?, ?, {placeholders})",
            [int(host_id), ts, *values],
        )
        conn.commit()
    finally:
        conn.close()


def sample_now(sample: dict[str, Any], host_id: int = LOCAL_HOST_ID) -> int:
    """Guarda la muestra usando el instante actual como marca de tiempo."""
    ts = int(time.time())
    insert_sample(ts, sample, host_id)
    return ts


# ============================== Agregados ==============================
def build_rollup(period: str, bucket: int, host_id: int = LOCAL_HOST_ID) -> int:
    """Recalcula el agregado del bucket ``bucket`` a partir del nivel inferior.

    Se usa ``INSERT OR REPLACE``: recalcular un bucket ya existente lo sobrescribe
    con datos más completos, lo que hace la operación idempotente y
    autorreparable (si faltaban muestras por un reinicio, se rellenan después).
    """
    if period not in PERIOD_SECONDS:
        raise ValueError(f"Periodo de agregado no soportado: {period}")
    span = PERIOD_SECONDS[period]
    host = int(host_id)
    if period == "1m":
        # El nivel inferior son las muestras crudas (no tienen columna de periodo).
        source = "metric_samples"
        source_filter = ""
        source_args: list[Any] = []
    else:
        # En ``metric_rollups`` el periodo de la fila **es** el de los datos que
        # promedian, así que el padre se filtra por su propio periodo.
        source = "metric_rollups"
        source_filter = "AND period = ?"
        source_args = [PARENT_PERIOD[period]]
    averages = ", ".join(f"AVG({name}) AS {name}" for name in METRIC_COLUMNS)
    columns = ", ".join(METRIC_COLUMNS)
    conn = db.connect()
    try:
        cursor = conn.execute(
            f"INSERT OR REPLACE INTO metric_rollups (host_id, period, ts, {columns}) "
            f"SELECT ?, ?, CAST(ts / ? AS INTEGER) * ?, {averages} "
            f"FROM {source} WHERE host_id = ? AND ts >= ? AND ts < ? {source_filter} "
            f"GROUP BY CAST(ts / ? AS INTEGER)",
            [
                host,
                period,
                span,
                span,
                host,
                bucket,
                bucket + span,
                *source_args,
                span,
            ],
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def build_rollup_if_due(
    period: str, host_id: int = LOCAL_HOST_ID, now: int | None = None
) -> int:
    """Reconstruye el último bucket cerrado de ``period`` si aún no se hizo.

    Es la vía que usan los agentes remotos: al llegar una muestra, el hub
    consolida los buckets ya cerrados (cada minuto para ``1m``, etc.) en lugar
    de esperar a su propio temporizador. Devuelve las filas escritas (0 si el
    bucket ya estaba construido).
    """
    if period not in PERIOD_SECONDS:
        raise ValueError(f"Periodo de agregado no soportado: {period}")
    moment = int(now if now is not None else time.time())
    bucket = last_complete_bucket(period, moment)
    key = (int(host_id), period)
    if _built_buckets.get(key) == bucket:
        return 0
    written = build_rollup(period, bucket, host_id)
    _built_buckets[key] = bucket
    return written


def reset_rollup_bookkeeping() -> None:
    """Olvida los buckets ya reconstruidos (tests y arranque del proceso)."""
    _built_buckets.clear()


def last_complete_bucket(period: str, now: int | None = None) -> int:
    """Devuelve el último bucket ya cerrado de ``period``."""
    span = PERIOD_SECONDS[period]
    moment = int(now if now is not None else time.time())
    return ((moment // span) - 1) * span


# ============================== Purga ==============================
def prune(now: int | None = None) -> dict[str, int]:
    """Borra los datos que superan la retención y devuelve las filas purgadas."""
    moment = int(now if now is not None else time.time())
    removed: dict[str, int] = {}
    conn = db.connect()
    try:
        removed["samples"] = conn.execute(
            "DELETE FROM metric_samples WHERE ts < ?",
            (moment - RETENTION_SECONDS["raw"],),
        ).rowcount
        for period in ROLLUP_PERIODS:
            removed[period] = conn.execute(
                "DELETE FROM metric_rollups WHERE period = ? AND ts < ?",
                (period, moment - RETENTION_SECONDS[period]),
            ).rowcount
        conn.commit()
    finally:
        conn.close()
    rollups = sum(removed[period] for period in ROLLUP_PERIODS)
    if removed["samples"] or rollups:
        logger.info(
            "Purga de métricas: {} muestras y {} agregados antiguos eliminados",
            removed["samples"],
            rollups,
        )
    return removed


# ============================ Consultas ============================
def _iso(ts: int) -> str:
    """Marca temporal Unix -> ISO-8601 UTC (segundos)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def period_for_range(range_key: str) -> str:
    """Nivel de la pirámide usado para representar un rango de la API."""
    if range_key not in RANGE_PERIODS:
        raise ValueError(f"Rango no soportado: {range_key}")
    return RANGE_PERIODS[range_key]


def _table_for_period(period: str) -> str:
    """Tabla física que corresponde a un periodo (``raw`` = muestras)."""
    return "metric_samples" if period == "raw" else "metric_rollups"


def fetch_series(
    period: str,
    since: int,
    until: int,
    metrics: tuple[str, ...],
    max_points: int = MAX_POINTS,
    host_id: int = LOCAL_HOST_ID,
) -> dict[str, list[dict[str, Any]]]:
    """Lee las filas de un rango y devuelve una serie por métrica solicitada.

    Cuando el rango contiene más de ``max_points`` filas se aplica un remuestreo
    uniforme (una muestra por bloque, más el último dato real) para no saturar
    ni el navegador ni la SD.
    """
    table = _table_for_period(period)
    where = "host_id = ? AND ts >= ? AND ts <= ?"
    args: list[Any] = [int(host_id), since, until]
    if period != "raw":
        where = "host_id = ? AND period = ? AND ts >= ? AND ts <= ?"
        args = [int(host_id), period, since, until]
    columns = ", ".join(("ts", *metrics))
    conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT {columns} FROM {table} WHERE {where} ORDER BY ts ASC", args
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    stride = max(1, math.ceil(total / max_points)) if max_points else 1
    selected = rows[::stride]
    if total and selected and selected[-1]["ts"] != rows[-1]["ts"]:
        # El último dato real siempre se incluye: una gráfica nunca debe
        # quedarse sin el valor más reciente.
        selected = [*selected[:-1], rows[-1]]
    return {metric: _points(selected, metric) for metric in metrics}


def _points(rows: Any, metric: str) -> list[dict[str, Any]]:
    """Convierte filas en la lista de puntos ``{ts, value}`` de una métrica."""
    return [
        {
            "ts": int(row["ts"]),
            "value": None if row[metric] is None else round(float(row[metric]), 2),
        }
        for row in rows
    ]


def history(
    range_key: str = "24h",
    metrics: tuple[str, ...] = ("cpu", "ram"),
    now: int | None = None,
    max_points: int = MAX_POINTS,
    host_id: int = LOCAL_HOST_ID,
) -> dict[str, Any]:
    """Devuelve el histórico downsampled del rango pedido.

    Returns:
        ``{range, period, metrics, points, from_ts, to_ts, host_id, series}``,
        donde ``series`` mapea cada métrica a su lista de puntos ``{ts, value}``.
        Solo incluye las filas del host pedido (por defecto, el local).
    """
    period = period_for_range(range_key)
    moment = int(now if now is not None else time.time())
    until = moment
    since = moment - RANGE_SECONDS[range_key]
    series = fetch_series(
        period, since, until, metrics, max_points=max_points, host_id=host_id
    )
    points = max((len(values) for values in series.values()), default=0)
    return {
        "range": range_key,
        "period": period,
        "metrics": list(metrics),
        "points": points,
        "from_ts": since,
        "to_ts": until,
        "host_id": int(host_id),
        "series": series,
    }


def export_rows(
    range_key: str = "24h",
    metrics: tuple[str, ...] = METRIC_COLUMNS,
    now: int | None = None,
    host_id: int = LOCAL_HOST_ID,
) -> tuple[str, list[str], list[tuple[Any, ...]]]:
    """Devuelve ``(period, cabeceras, filas)`` para la exportación CSV.

    A diferencia de :func:`history` no se remuestrea: el CSV es la extracción
    completa del rango (el administrador quiere los datos, no una gráfica).
    """
    period = period_for_range(range_key)
    moment = int(now if now is not None else time.time())
    since = moment - RANGE_SECONDS[range_key]
    table = _table_for_period(period)
    where = "host_id = ? AND ts >= ? AND ts <= ?"
    args: list[Any] = [int(host_id), since, moment]
    if period != "raw":
        where = "host_id = ? AND period = ? AND ts >= ? AND ts <= ?"
        args = [int(host_id), period, since, moment]
    columns = ", ".join(("ts", *metrics))
    conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT {columns} FROM {table} WHERE {where} ORDER BY ts ASC", args
        ).fetchall()
    finally:
        conn.close()
    header = ["timestamp", "iso", *metrics]
    body = [
        (int(row["ts"]), _iso(int(row["ts"])), *(row[name] for name in metrics))
        for row in rows
    ]
    return period, header, body


__all__ = [
    "LOCAL_HOST_ID",
    "MAX_POINTS",
    "METRIC_COLUMNS",
    "PERIOD_SECONDS",
    "RANGE_PERIODS",
    "RANGE_SECONDS",
    "RETENTION_SECONDS",
    "ROLLUP_PERIODS",
    "build_rollup",
    "build_rollup_if_due",
    "export_rows",
    "fetch_series",
    "history",
    "insert_sample",
    "last_complete_bucket",
    "period_for_range",
    "prune",
    "reset_rollup_bookkeeping",
    "sample_now",
]