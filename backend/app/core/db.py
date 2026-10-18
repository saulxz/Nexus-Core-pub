"""Esquema SQLite de métricas, alertas y monitores, con migraciones idempotentes.

Nexus-Core comparte una única base de datos (``$NEXUS_DATA_DIR/nexus.db``) con
la autenticación del panel. Este módulo es el propietario del esquema de
*telemetría y alertas* y aplica las migraciones al arrancar:

* Cada migración es SQL idempotente (``CREATE TABLE IF NOT EXISTS`` /
  ``CREATE INDEX IF NOT EXISTS``): aplicarlas dos veces no falla ni duplica nada.
* La versión aplicada se guarda en la tabla ``schema_version``; ``migrate()``
  ejecuta únicamente las migraciones pendientes.
* Nunca se hace ``DROP`` ni ``ALTER`` de tablas existentes: una migración nueva
  solo puede añadir objetos. Si una tabla ya existe (p. ej. creada a mano por una
  versión anterior del proyecto), ``IF NOT EXISTS`` la deja intacta.
* ``PRAGMA user_version`` se mantiene sincronizado como respaldo para
  inspeccionar el esquema con la CLI de SQLite.

Concurrencia: la app escribe desde varios hilos (colector, endpoints) y desde
el plano asíncrono, por lo que la conexión se abre en modo WAL (lectores y
escritor simultáneos) y con ``busy_timeout`` (espera ante bloqueos en lugar de
fallar con «database is locked»).
"""

from __future__ import annotations

import platform
import socket
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging_conf import logger
from app.core.security import connect as _auth_connect

#: Espera máxima (ms) ante un bloqueo de escritura de otro proceso/hilo.
BUSY_TIMEOUT_MS = 5000


def connect() -> sqlite3.Connection:
    """Abre la base de datos de Nexus-Core lista para lectura y escritura.

    Reutiliza la conexión base de :mod:`app.core.security` (WAL + filas como
    diccionario) y añade ``busy_timeout`` y ``synchronous=NORMAL``, que es el
    punto óptimo en WAL: durability suficiente para métricas y muy superior en
    escritura (importante en la SD de una Raspberry Pi).
    """
    conn = _auth_connect()
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# ============================== Migraciones ==============================
# Cada entrada es un bloque SQL idempotente que se aplica una única vez.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        -- Muestra en bruto cada 5 s (una fila por marca de tiempo).
        CREATE TABLE IF NOT EXISTS metric_samples (
            ts    INTEGER PRIMARY KEY,
            cpu   REAL,
            ram   REAL,
            swap  REAL,
            disk  REAL,
            load1 REAL,
            temp  REAL
        );
        -- Agregados por periodo: 1m (desde muestras), 5m (desde 1m),
        -- 1h (desde 5m). El periodo forma parte de la clave primaria.
        CREATE TABLE IF NOT EXISTS metric_rollups (
            period TEXT    NOT NULL,
            ts      INTEGER NOT NULL,
            cpu     REAL,
            ram     REAL,
            swap    REAL,
            disk    REAL,
            load1   REAL,
            temp    REAL,
            PRIMARY KEY (period, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_rollups_period_ts
            ON metric_rollups(period, ts);

        -- Reglas de alerta evaluadas por el motor en cada muestra.
        CREATE TABLE IF NOT EXISTS alert_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            metric          TEXT NOT NULL,
            op              TEXT NOT NULL CHECK (op IN ('>', '<')),
            threshold       REAL NOT NULL,
            clear_threshold REAL,
            duration_s      INTEGER NOT NULL DEFAULT 0,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled
            ON alert_rules(enabled);

        -- Eventos: uno abierto como máximo por regla (ver app.modules.alerts).
        CREATE TABLE IF NOT EXISTS alert_events (
            id            INTEGER PRIMARY KEY,
            rule_id       INTEGER,
            metric        TEXT,
            status        TEXT    NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'acked', 'closed')),
            severity      TEXT    NOT NULL DEFAULT 'warn'
                          CHECK (severity IN ('warn', 'crit')),
            value         REAL,
            threshold     REAL,
            opened_at     TEXT,
            closed_at     TEXT,
            acked_at      TEXT,
            acked_by      TEXT,
            snooze_until  TEXT,
            suppressed    INTEGER NOT NULL DEFAULT 0,
            message       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alert_events_status_opened
            ON alert_events(status, opened_at);
        CREATE INDEX IF NOT EXISTS idx_alert_events_rule
            ON alert_events(rule_id, status);
        CREATE INDEX IF NOT EXISTS idx_alert_events_metric
            ON alert_events(metric);
        """,
    ),
    (
        2,
        """
        -- Canales de notificación (telegram | webhook | smtp).
        CREATE TABLE IF NOT EXISTS notification_channels (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            kind       TEXT NOT NULL CHECK (kind IN ('telegram', 'webhook', 'smtp')),
            config_json TEXT NOT NULL DEFAULT '{}',
            enabled    INTEGER NOT NULL DEFAULT 1,
            last_ok_at TEXT,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        -- Ventanas de mantenimiento: se registran los eventos pero no se
        -- notifica (columna suppressed=1 del evento).
        CREATE TABLE IF NOT EXISTS maintenance_windows (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            starts_at  TEXT NOT NULL,
            ends_at    TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_maintenance_enabled
            ON maintenance_windows(enabled, starts_at, ends_at);
        """,
    ),
    (
        3,
        """
        -- Monitores de disponibilidad: comprobaciones periódicas de un destino
        -- (http | tcp | ssl) definidas por el administrador del panel.
        CREATE TABLE IF NOT EXISTS monitors (
            id              INTEGER PRIMARY KEY,
            name            TEXT    NOT NULL,
            kind            TEXT    NOT NULL CHECK (kind IN ('http', 'tcp', 'ssl')),
            target          TEXT    NOT NULL,
            port            INTEGER,
            interval_s      INTEGER NOT NULL DEFAULT 60,
            timeout_s       INTEGER NOT NULL DEFAULT 5,
            expected_status INTEGER,
            keyword         TEXT,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_monitors_enabled
            ON monitors(enabled);

        -- Un heartbeat por comprobación. Es el histórico que alimenta los
        -- porcentajes de disponibilidad y la gráfica de barras de estado.
        CREATE TABLE IF NOT EXISTS heartbeats (
            id          INTEGER PRIMARY KEY,
            monitor_id  INTEGER NOT NULL,
            ts          INTEGER NOT NULL,
            up          INTEGER NOT NULL,
            latency_ms  REAL,
            status_code INTEGER,
            detail      TEXT,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_heartbeats_monitor_ts
            ON heartbeats(monitor_id, ts);
        """,
    ),
    (
        4,
        """
        -- Tokens de API para integraciones (MCP, scripts, Prometheus).
        -- Solo se guarda el SHA-256 del token y un prefijo legible: el secreto
        -- en claro se muestra UNA vez, al crearlo, y no vuelve a existir.
        CREATE TABLE IF NOT EXISTS api_tokens (
            id           INTEGER PRIMARY KEY,
            name         TEXT NOT NULL,
            prefix       TEXT NOT NULL,
            token_hash   TEXT NOT NULL UNIQUE,
            scope        TEXT NOT NULL DEFAULT 'read'
                         CHECK (scope IN ('read', 'write')),
            created_at   TEXT,
            last_used_at TEXT,
            expires_at   TEXT,
            revoked_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

        -- Líneas base del FIM (integridad de ficheros de configuración).
        -- El contenido se guarda para poder mostrar un diff unificado legible.
        CREATE TABLE IF NOT EXISTS config_baselines (
            path       TEXT PRIMARY KEY,
            sha256     TEXT NOT NULL,
            content    TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        5,
        """
        -- Inventario de hosts de la flota. El host local (id=1) se inserta en
        -- _migrate_fleet_tables: es el servidor donde corre el hub, de modo que
        -- el modo local sigue siendo «el host 1» y nada cambia para el panel.
        CREATE TABLE IF NOT EXISTS hosts (
            id            INTEGER PRIMARY KEY,
            name          TEXT    NOT NULL,
            hostname      TEXT,
            os            TEXT,
            agent_version TEXT,
            -- Solo el SHA-256 del token del agente: el secreto se entrega UNA
            -- vez al enrolar y no vuelve a existir en el hub.
            token_hash    TEXT UNIQUE,
            token_prefix  TEXT,
            status        TEXT    NOT NULL DEFAULT 'offline',
            last_seen_ts  INTEGER,
            enrolled_at   TEXT,
            notes         TEXT,
            enabled       INTEGER NOT NULL DEFAULT 1,
            is_local      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_hosts_enabled ON hosts(enabled);
        CREATE INDEX IF NOT EXISTS idx_hosts_last_seen ON hosts(last_seen_ts);

        -- Cola de comandos: el hub encola y el agente sondea (long-poll).
        CREATE TABLE IF NOT EXISTS commands (
            id          INTEGER PRIMARY KEY,
            host_id     INTEGER NOT NULL,
            tool        TEXT    NOT NULL,
            args_json   TEXT,
            status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'done', 'error', 'expired')),
            actor       TEXT,
            created_at  TEXT,
            sent_at     TEXT,
            finished_at TEXT,
            result_json TEXT,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_commands_host_status
            ON commands(host_id, status, id);

        -- Última instantánea conocida de cada host (reservada para la fase 3:
        -- inventario de contenedores/servicios con el que responder sin
        -- preguntar al agente). Por ahora la escribe el heartbeat.
        CREATE TABLE IF NOT EXISTS host_state (
            host_id     INTEGER PRIMARY KEY,
            ts          INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );
        """,
    ),
)

#: Migraciones de columnas: ``(version, tabla, ((columna, tipo), ...))``.
#:
#: ``ALTER TABLE ... ADD COLUMN`` **no** es idempotente en SQLite (falla si la
#: columna ya existe), así que se declaran aparte y se aplican comprobando antes
#: ``PRAGMA table_info``. Solo se añaden columnas: nunca se altera ni se borra
#: ninguna, de modo que una base existente mantiene sus datos.
COLUMN_MIGRATIONS: tuple[tuple[int, str, tuple[tuple[str, str], ...]], ...] = (
    # Segundo factor (TOTP) del administrador, con códigos de recuperación.
    (4, "admin", (("totp_secret", "TEXT"), ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"), ("totp_recovery", "TEXT"))),
    # Acción de autorremediación propuesta al abrirse un evento de la regla.
    (4, "alert_rules", (("remediation_action", "TEXT"),)),
    # Ámbito de la regla: NULL = se evalúa en todos los hosts (regla global);
    # con valor = solo en ese host. El evento abierto hereda el host evaluado.
    (5, "alert_rules", (("host_id", "INTEGER"),)),
    (5, "alert_events", (("host_id", "INTEGER"),)),
)

#: Host que representa al propio servidor del hub (el «host local» del panel).
LOCAL_HOST_ID = 1

#: Definición de ``metric_rollups`` con la clave primaria por host. Se usa en la
#: reconstrucción de la migración 5 (las muestras y los agregados pasan a estar
#: particionados por host).
_ROLLUPS_DDL = """
    CREATE TABLE metric_rollups (
        host_id INTEGER NOT NULL DEFAULT 1,
        period  TEXT    NOT NULL,
        ts      INTEGER NOT NULL,
        cpu     REAL,
        ram     REAL,
        swap    REAL,
        disk    REAL,
        load1   REAL,
        temp    REAL,
        PRIMARY KEY (host_id, period, ts)
    )
"""

_SAMPLES_DDL = """
    CREATE TABLE metric_samples (
        host_id INTEGER NOT NULL DEFAULT 1,
        ts      INTEGER NOT NULL,
        cpu     REAL,
        ram     REAL,
        swap    REAL,
        disk    REAL,
        load1   REAL,
        temp    REAL,
        PRIMARY KEY (host_id, ts)
    )
"""

#: Métricas almacenadas en las muestras y los agregados.
_METRICS = ("cpu", "ram", "swap", "disk", "load1", "temp")

#: Tablas que la migración 5 reconstruye porque su clave primaria debe pasar a
#: incluir ``host_id`` (``tabla``, ``nombre temporal``, ``DDL``, ``columnas``).
_HOST_PARTITIONED_TABLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("metric_rollups", "metric_rollups_legacy", _ROLLUPS_DDL, ("period", "ts", *_METRICS)),
    ("metric_samples", "metric_samples_legacy", _SAMPLES_DDL, ("ts", *_METRICS)),
)


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Número de filas de una tabla (0 si no existe)."""
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"] or 0)


def _partition_metrics_by_host(
    conn: sqlite3.Connection,
    table: str,
    legacy: str,
    ddl: str,
    columns: tuple[str, ...],
) -> bool:
    """Reconstruye ``table`` con ``host_id`` en la clave primaria.

    Antes de la migración 5 el histórico era de un único host, así que su clave
    primaria era ``(period, ts)`` / ``ts``: con varios agentes, dos hosts que
    enviasen muestra en el mismo segundo se pisarían. La reconstrucción copia
    todas las filas existentes al host local (``host_id = 1``) y **verifica que
    no se pierda ninguna**; si el recuento no cuadra se lanza el error, la
    transacción se deshace y la migración se reintenta en el próximo arranque.

    Es idempotente: si ``host_id`` ya forma parte de la tabla (base nueva,
    migración ya aplicada) no se toca nada.
    """
    present = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if "host_id" in present:
        return False
    # La copia ocurre dentro de la misma transacción, así que un fallo no deja
    # restos; el DROP evita además que una ejecución anterior interrumpida antes
    # de confirmar bloquee el nombre temporal.
    conn.execute(f"DROP TABLE IF EXISTS {legacy}")
    before = _count_rows(conn, table)
    conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
    conn.execute(ddl)
    column_list = ", ".join(columns)
    conn.execute(
        f"INSERT INTO {table} (host_id, {column_list}) "
        f"SELECT {LOCAL_HOST_ID}, {column_list} FROM {legacy}"
    )
    after = _count_rows(conn, table)
    if after != before:
        raise sqlite3.Error(
            f"Reconstrucción de '{table}': se esperaban {before} filas y se han copiado {after}."
        )
    conn.execute(f"DROP TABLE {legacy}")
    logger.info(
        "Tabla '{}' reconstruida particionada por host: {} filas heredadas al host {}.",
        table,
        before,
        LOCAL_HOST_ID,
    )
    return True


def _migrate_fleet_tables(conn: sqlite3.Connection) -> None:
    """Datos de la migración 5: host local y particionado del histórico.

    * Inserta el host local (id=1) si falta, para que el panel siga viendo sus
      propias métricas como siempre (host 1) aunque no haya ningún agente.
    * Reconstruye ``metric_samples`` y ``metric_rollups`` con ``host_id``.
    * Recrea los índices de agregados (el antiguo ``idx_rollups_period_ts`` viaja
      con la tabla renombrada y se recrea sobre la nueva).
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local = conn.execute("SELECT id FROM hosts WHERE id = ?", (LOCAL_HOST_ID,)).fetchone()
    if local is None:
        conn.execute(
            "INSERT INTO hosts (id, name, hostname, os, status, enrolled_at, enabled, is_local) "
            "VALUES (?, ?, ?, ?, 'online', ?, 1, 1)",
            (
                LOCAL_HOST_ID,
                "Este servidor",
                socket.gethostname(),
                platform.system() or "desconocido",
                stamp,
            ),
        )
        logger.info("Host local creado con id={} ('Este servidor')", LOCAL_HOST_ID)

    for table, legacy, ddl, columns in _HOST_PARTITIONED_TABLES:
        _partition_metrics_by_host(conn, table, legacy, ddl, columns)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rollups_period_ts ON metric_rollups(period, ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rollups_host_period_ts "
        "ON metric_rollups(host_id, period, ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_host_ts ON metric_samples(host_id, ts)"
    )


def _apply_column_migration(
    conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> None:
    """Añade las columnas que falten de ``table`` (idempotente).

    Si la tabla no existe todavía (p. ej. ``db.migrate()`` se llama antes que
    ``security.init_db()``) no hace nada: la tabla se creará después con la
    definición ya actualizada.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        logger.debug("Migración de columnas omitida: la tabla '{}' no existe", table)
        return
    present = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, ddl in columns:
        if column in present:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("Columna añadida: {}.{}", table, column)


#: Migraciones de datos: transformaciones que no se pueden expresar en el bloque
#: SQL idempotente (reconstruir una tabla para cambiar su clave primaria). Se
#: ejecutan con la misma conexión y dentro de la misma transacción que su
#: migración, así que un fallo deja la base intacta.
DATA_MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (5, _migrate_fleet_tables),
)


#: Versión de esquema objetivo: la de la última migración declarada.
SCHEMA_VERSION = max(
    max(version for version, _script in MIGRATIONS),
    max(version for version, _table, _columns in COLUMN_MIGRATIONS),
    max(version for version, _action in DATA_MIGRATIONS),
)


def _current_version(conn: sqlite3.Connection) -> int:
    """Devuelve la versión de esquema aplicada (0 si es una base nueva)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version    INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def migrate() -> int:
    """Aplica las migraciones pendientes y devuelve la versión resultante.

    Es idempotente: llamarla en cada arranque no modifica nada cuando el
    esquema ya está al día.
    """
    conn = connect()
    try:
        version = _current_version(conn)
        for target, script in MIGRATIONS:
            if target <= version:
                continue
            try:
                conn.executescript(script)
                for column_version, table, columns in COLUMN_MIGRATIONS:
                    if column_version == target:
                        _apply_column_migration(conn, table, columns)
                for data_version, action in DATA_MIGRATIONS:
                    if data_version == target:
                        action(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version, applied_at) "
                    "VALUES (?, ?)",
                    (target, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                )
                conn.execute(f"PRAGMA user_version = {target}")
                conn.commit()
            except sqlite3.Error as exc:
                # Una migración fallida no debe impedir arrancar el panel: el
                # resto del esquema sigue válido y la de autenticación es
                # independiente (la crea app.core.security).
                conn.rollback()
                logger.error(
                    "No se pudo aplicar la migración {} del esquema: {}", target, exc
                )
                return version
            logger.info("Migración de esquema aplicada: {}", target)
            version = target
        return version
    finally:
        conn.close()


def table_names() -> set[str]:
    """Devuelve los nombres de tabla existentes (comprobación/diagnóstico)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}
    finally:
        conn.close()


def next_id(conn: sqlite3.Connection, table: str) -> int:
    """Calcula el siguiente id correlativo de una tabla de id entero.

    Las tablas de este módulo (``alert_events``, ``notification_channels``,
    ``maintenance_windows``) usan ``INTEGER PRIMARY KEY`` sin AUTOINCREMENT:
    SQLite asigna el id automáticamente al insertar ``NULL``, pero necesitamos
    conocerlo de antemano para usarlo como clave foránea lógica.
    """
    row = conn.execute(f"SELECT MAX(id) AS m FROM {table}").fetchone()
    return int(row["m"] or 0) + 1


def row_to_dict(row: Any) -> dict[str, Any]:
    """Convierte una fila de SQLite en diccionario (filas vacías → ``{}``)."""
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


__all__ = [
    "BUSY_TIMEOUT_MS",
    "COLUMN_MIGRATIONS",
    "DATA_MIGRATIONS",
    "LOCAL_HOST_ID",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "connect",
    "migrate",
    "next_id",
    "row_to_dict",
    "table_names",
]