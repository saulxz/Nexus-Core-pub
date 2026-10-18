"""Tests del esquema SQLite y del histórico de métricas (Fase: datos).

Ejecutar con:
    python -m pytest -q
"""

from __future__ import annotations

import time

import pytest

from app.core import db
from app.modules import metrics_store

#: Todas las tablas que deben existir tras aplicar las migraciones.
EXPECTED_TABLES = {
    "schema_version",
    "metric_samples",
    "metric_rollups",
    "alert_rules",
    "alert_events",
    "notification_channels",
    "maintenance_windows",
}


def _sample(ts: int, cpu: float = 10.0, temp: float | None = 50.0) -> dict[str, float | None]:
    """Muestra de prueba con todas las métricas."""
    return {
        "cpu": cpu,
        "ram": 20.0,
        "swap": 0.0,
        "disk": 42.0,
        "load1": 0.4,
        "temp": temp,
    }


# ============================== Migraciones ==============================
def test_migrate_creates_every_table() -> None:
    """Las migraciones crean las seis tablas de métricas y alertas."""
    assert db.migrate() == db.SCHEMA_VERSION
    assert EXPECTED_TABLES <= db.table_names()


def test_migrate_is_idempotent() -> None:
    """Aplicar las migraciones varias veces no falla ni duplica versiones."""
    assert db.migrate() == db.SCHEMA_VERSION
    assert db.migrate() == db.SCHEMA_VERSION
    conn = db.connect()
    try:
        versions = [row["version"] for row in conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        )]
    finally:
        conn.close()
    assert versions == list(range(1, db.SCHEMA_VERSION + 1))


def test_migrate_does_not_break_existing_tables() -> None:
    """Una tabla previa con datos sobrevive a las migraciones."""
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS prueba_migracion (id INTEGER PRIMARY KEY, dato TEXT)"
        )
        conn.execute(
            "INSERT INTO prueba_migracion (id, dato) VALUES (1, 'sobrevive')"
        )
        conn.commit()
    finally:
        conn.close()

    assert db.migrate() == db.SCHEMA_VERSION

    conn = db.connect()
    try:
        row = conn.execute("SELECT dato FROM prueba_migracion WHERE id = 1").fetchone()
        assert row["dato"] == "sobrevive"
    finally:
        conn.close()


def test_connect_enables_wal_and_busy_timeout() -> None:
    """La conexión usa WAL y espera ante bloqueos (colector + API en paralelo)."""
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == db.BUSY_TIMEOUT_MS
    finally:
        conn.close()


# ========================== Muestras y agregados ==========================
def test_insert_sample_is_idempotent() -> None:
    """Guardar dos veces la misma marca de tiempo no duplica la fila."""
    ts = int(time.time())
    metrics_store.insert_sample(ts, _sample(ts, cpu=11.0))
    metrics_store.insert_sample(ts, _sample(ts, cpu=12.0))
    conn = db.connect()
    try:
        rows = conn.execute("SELECT cpu FROM metric_samples WHERE ts = ?", (ts,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["cpu"] == 12.0


def test_build_rollup_averages_each_bucket() -> None:
    """El agregado de 1 m es la media de las muestras del minuto."""
    base = (int(time.time()) // 60) * 60 - 120  # minuto ya cerrado
    for offset, value in enumerate((10.0, 20.0, 30.0)):
        metrics_store.insert_sample(base + offset * 10, _sample(base + offset * 10, cpu=value))

    metrics_store.build_rollup("1m", base)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT ts, cpu FROM metric_rollups WHERE period = '1m' AND ts = ?", (base,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["ts"] == base
    assert row["cpu"] == pytest.approx(20.0)


def test_rollups_chain_from_lower_tier() -> None:
    """5 m se calcula a partir de 1 m y 1 h a partir de 5 m."""
    base = (int(time.time()) // 300) * 300 - 600
    for minute in range(5):
        for offset in range(0, 60, 15):
            ts = base + minute * 60 + offset
            metrics_store.insert_sample(ts, _sample(ts, cpu=30.0 + minute))
    metrics_store.build_rollup("1m", base)
    metrics_store.build_rollup("1m", base + 60 * 4)
    metrics_store.build_rollup("5m", base)

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT cpu FROM metric_rollups WHERE period = '5m' AND ts = ?", (base,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    # 1m promedia los cinco minutos y 5m promedia esos promedios (32 = media de 30..34).
    assert row["cpu"] == pytest.approx(32.0, abs=0.5)


def test_last_complete_bucket_is_closed() -> None:
    """El bucket elegido por defecto ya ha terminado."""
    now = int(time.time())
    bucket = metrics_store.last_complete_bucket("1m", now)
    assert bucket % 60 == 0
    assert bucket + 60 <= now


# ============================== Retención ==============================
def test_prune_respects_retention() -> None:
    """La purga borra lo que supera la retención de cada nivel."""
    now = int(time.time())
    old_raw = now - metrics_store.RETENTION_SECONDS["raw"] - 3600
    fresh_raw = now - 60
    metrics_store.insert_sample(old_raw, _sample(old_raw))
    metrics_store.insert_sample(fresh_raw, _sample(fresh_raw))
    old_1m = now - metrics_store.RETENTION_SECONDS["1m"] - 600
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO metric_rollups (period, ts, cpu) VALUES ('1m', ?, 5.0)",
            (old_1m,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metric_rollups (period, ts, cpu) VALUES ('1m', ?, 6.0)",
            (now - 60,),
        )
        conn.commit()
    finally:
        conn.close()

    removed = metrics_store.prune(now)

    assert removed["samples"] >= 1
    assert removed["1m"] >= 1
    conn = db.connect()
    try:
        remaining = {
            row["ts"]
            for row in conn.execute("SELECT ts FROM metric_samples WHERE ts >= ?", (now - 7200,))
        }
        rolled = {
            row["ts"]
            for row in conn.execute(
                "SELECT ts FROM metric_rollups WHERE period = '1m' AND ts >= ?", (now - 7200,)
            )
        }
    finally:
        conn.close()
    assert fresh_raw in remaining
    assert old_raw not in remaining
    assert (now - 60) in rolled


# ======================= Rango y nivel de agregado =======================
@pytest.mark.parametrize(
    ("range_key", "period"),
    [("1h", "1m"), ("24h", "1m"), ("7d", "5m"), ("30d", "1h")],
)
def test_period_for_range(range_key: str, period: str) -> None:
    """Cada rango se sirve desde el nivel de la pirámide que le corresponde."""
    assert metrics_store.period_for_range(range_key) == period


def test_period_for_range_rejects_unknown() -> None:
    """Un rango no soportado se rechaza con ValueError."""
    with pytest.raises(ValueError):
        metrics_store.period_for_range("90d")


def test_history_uses_the_tier_of_the_range() -> None:
    """El histórico lee del nivel correcto y devuelve las métricas pedidas."""
    now = int(time.time())
    bucket = now - 300
    conn = db.connect()
    try:
        # Un valor solo en 5m: si el rango 24h (nivel 1m) lo devolviera, la
        # lectura estaría usando la tabla equivocada.
        conn.execute(
            "INSERT OR REPLACE INTO metric_rollups (period, ts, cpu, ram) "
            "VALUES ('5m', ?, 77.0, 11.0)",
            (bucket,),
        )
        conn.commit()
    finally:
        conn.close()

    day = metrics_store.history("24h", ("cpu",), now=now)
    week = metrics_store.history("7d", ("cpu",), now=now)

    assert day["period"] == "1m"
    assert all(point["value"] != 77.0 for point in day["series"]["cpu"])
    assert week["period"] == "5m"
    assert any(point["value"] == 77.0 for point in week["series"]["cpu"])


def test_history_downsamples_to_max_points() -> None:
    """Una ventana enorme se remuestrea para no pasar de 1000 puntos."""
    now = int(time.time())
    rows = [(now - index * 3600, float(index)) for index in range(1, 900)]
    conn = db.connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO metric_rollups (period, ts, cpu) VALUES ('1h', ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    result = metrics_store.history("30d", ("cpu",), now=now, max_points=1000)

    assert result["period"] == "1h"
    assert 0 < result["points"] <= metrics_store.MAX_POINTS
    # El último dato real siempre está presente.
    assert result["series"]["cpu"][-1]["ts"] == rows[0][0]


def test_export_rows_has_header_and_rows() -> None:
    """La exportación CSV devuelve cabecera y filas del rango."""
    now = int(time.time())
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO metric_rollups (period, ts, cpu) VALUES ('1h', ?, 12.5)",
            (now - 60,),
        )
        conn.commit()
    finally:
        conn.close()

    period, header, body = metrics_store.export_rows("30d", ("cpu", "ram"), now=now)

    assert period == "1h"
    assert header[:2] == ["timestamp", "iso"]
    assert any(row[2] == 12.5 for row in body)