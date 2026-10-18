"""Motor de alertas: reglas, eventos, histéresis y ventanas de mantenimiento.

Evaluado en cada muestra (cada 5 s), :func:`evaluate` recorre las reglas
habilitadas y decide si hay que abrir, actualizar o cerrar un evento.

Máquina de estados por regla (el estado vivo —instante de inicio del
incumplimiento— vive en memoria, en :data:`_state`):

    OK ──incumplimiento──▶ PENDIENTE ──duración cumplida──▶ ABIERTO ──umbral
                                                                  de recuperación──▶ CERRADO
        ▲                    │                                │
        └───valor OK──────────┘                                └──ack──▶ ACKNOWLEDGED

* **Duración** (``duration_s``): el incumplimiento debe ser continuo durante
  ese número de segundos antes de abrir el evento. Con ``duration_s = 0`` el
  evento se abre en la primera muestra que incumple. Un valor que vuelve a la
  normalidad reinicia el contador.
* **Dedupe**: hay como máximo **un evento abierto por regla y host**
  (``status != 'closed'``). Si la regla vuelve a incumplir con un evento
  abierto, solo se actualizan ``value``/``message``; no se notifica dos veces.
  La recuperación de un host no afecta a los demás: cada agente lleva su
  propio estado vivo.
* **Ámbito por host** (``alert_rules.host_id``): ``NULL`` = regla global, se
  evalúa en **todos** los hosts; con valor = solo en ese host. Los eventos
  abiertos heredan el host evaluado, de forma que el panel puede decir de qué
  máquina viene la incidencia. El host evaluado es el local (id 1) cuando se
  llama sin ``host_id`` (modo local: el comportamiento de siempre).
* **Histéresis**: el evento se cierra cuando el valor cruza ``clear_threshold``
  (por defecto igual a ``threshold``). Con un ``clear_threshold`` distinto del
  umbral (p. ej. umbral 90 %, recuperación 80 %) el valor puede quedar en una
  zona intermedia sin abrir ni cerrar nada: eso es exactamente lo que evita el
  *flapping* de una Raspberry Pi saturada.
* **Severidad** (regla documentada):
  ``crit`` si la regla **no** define ``clear_threshold`` (no hay banda de
  recuperación: cualquier incumplimiento es un fallo duro, p. ej. temp > 85 °C)
  o si el valor se desvía un 25 % o más del umbral; ``warn`` en el resto de
  casos (cruce suave, dentro o cerca de la banda de histéresis).
* **Mantenimiento**: si hay una ventana activa, el evento **se registra** con
  ``suppressed = 1`` pero no se notifica (evita ruido por reinicios previstos).
* **Snooze**: silencia la apertura de nuevos eventos de esa regla hasta
  ``snooze_until`` y reinicia el contador de duración, de forma que al expirar
  el aviso vuelve a pedir el incumplimiento continuo (no un pico aislado).

Cuando el estado en memoria se pierde (reinicio del servicio) el motor arranca
limpio: la siguiente muestra reinicia la cuenta de duración. Los eventos ya
abiertos siguen abiertos porque viven en la base de datos.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from app.core import db
from app.core.audit import audit
from app.core.db import LOCAL_HOST_ID
from app.core.logging_conf import logger
from app.modules.metrics_store import METRIC_COLUMNS

#: Estados posibles de un evento.
EVENT_STATUSES: tuple[str, ...] = ("open", "acked", "closed")

#: Severidades posibles de un evento.
EVENT_SEVERITIES: tuple[str, ...] = ("warn", "crit")

#: Desvío mínimo respecto al umbral para considerar un incumplimiento crítico.
CRIT_OVERSHOOT = 0.25

#: Estado vivo por regla **y host** (se reinicia al reiniciar el servicio). La
#: clave es ``(rule_id, host_id)`` para que el incumplimiento sostenido de una
#: máquina no satisfaga el contador de duración de otra.
_state: dict[tuple[int, int], dict[str, Any]] = {}


def rule_applies_to(rule: dict[str, Any], host_id: int) -> bool:
    """True si la regla debe evaluarse para ese host.

    ``alert_rules.host_id`` a ``NULL`` significa «regla global»: se evalúa en
    todos los hosts de la flota. Con valor, solo en ese host.
    """
    scope = rule.get("host_id")
    return scope is None or int(scope) == int(host_id)


# =============================== Utilidades ===============================
def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utcnow() -> datetime:
    """Instante actual con zona horaria UTC explícita (igual que ``_now_iso``)."""
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    """Parsea una marca ISO-8601; devuelve ``None`` si no es válida."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _breached(rule: dict[str, Any], value: float) -> bool:
    """True si ``value`` incumple el operador de la regla."""
    threshold = float(rule["threshold"])
    return value > threshold if rule["op"] == ">" else value < threshold


def _recovered(rule: dict[str, Any], value: float) -> bool:
    """True si ``value`` ha vuelto a la zona segura (histéresis)."""
    clear = rule.get("clear_threshold")
    target = float(rule["threshold"]) if clear is None else float(clear)
    return value <= target if rule["op"] == ">" else value >= target


def severity_for(rule: dict[str, Any], value: float) -> str:
    """Calcula la severidad de un incumplimiento (regla documentada arriba).

    * ``crit``: la regla no define ``clear_threshold`` (fallo duro) o el valor
      se desvía un 25 % o más del umbral.
    * ``warn``: el resto (cruce suave, cerca o dentro de la banda).
    """
    if rule.get("clear_threshold") is None:
        return "crit"
    threshold = float(rule["threshold"])
    if rule["op"] == ">":
        deviation = (value - threshold) / max(abs(threshold), 1.0)
    else:
        deviation = (threshold - value) / max(abs(threshold), 1.0)
    return "crit" if deviation >= CRIT_OVERSHOOT else "warn"


def _describe(rule: dict[str, Any], value: float) -> str:
    """Mensaje legible del evento (en español)."""
    symbol = ">" if rule["op"] == ">" else "<"
    base = f"{rule['metric']} = {value:.1f} {symbol} {float(rule['threshold']):.1f}"
    if rule.get("clear_threshold") is not None:
        return f"{base} (recupera en {float(rule['clear_threshold']):.1f})"
    return base


# ================================ Reglas ================================
def list_rules(enabled_only: bool = False) -> list[dict[str, Any]]:
    """Devuelve las reglas de alerta (todas o solo las habilitadas)."""
    sql = "SELECT * FROM alert_rules"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    conn = db.connect()
    try:
        return [db.row_to_dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def get_rule(rule_id: int) -> dict[str, Any] | None:
    """Devuelve una regla por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def create_rule(data: dict[str, Any]) -> dict[str, Any]:
    """Crea una regla de alerta y devuelve la fila almacenada.

    ``data['host_id']`` a ``None`` deja la regla como **global** (se evalúa en
    todos los hosts de la flota); con un id, la regla es exclusiva de ese host.
    """
    stamp = _now_iso()
    scope = data.get("host_id")
    conn = db.connect()
    try:
        cursor = conn.execute(
            "INSERT INTO alert_rules (name, metric, op, threshold, clear_threshold, "
            "duration_s, enabled, remediation_action, host_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"],
                data["metric"],
                data["op"],
                float(data["threshold"]),
                None if data.get("clear_threshold") is None else float(data["clear_threshold"]),
                int(data.get("duration_s") or 0),
                1 if data.get("enabled", True) else 0,
                data.get("remediation_action"),
                None if scope is None else int(scope),
                stamp,
                stamp,
            ),
        )
        conn.commit()
        rule_id = int(cursor.lastrowid or 0)
    finally:
        conn.close()
    reset_state(rule_id)
    return get_rule(rule_id) or {}


def update_rule(rule_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Actualiza campos de una regla existente.

    Solo se tocan las columnas presentes en ``changes`` (la API las obtiene con
    ``model_dump(exclude_unset=True)``), de modo que un ``clear_threshold: null``
    explícito elimina la banda de histéresis y una clave ausente no cambia nada.
    """
    fields: list[str] = []
    values: list[Any] = []
    for column in ("name", "metric", "op", "threshold", "clear_threshold", "duration_s"):
        if column not in changes:
            continue
        value = changes[column]
        if value is None and column != "clear_threshold":
            continue  # Las columnas NOT NULL no se tocan con un null.
        if column in ("threshold", "clear_threshold"):
            value = None if value is None else float(value)
        elif column == "duration_s":
            value = int(value)
        fields.append(f"{column} = ?")
        values.append(value)
    if "host_id" in changes:
        # ``null`` devuelve la regla al ámbito global (todos los hosts).
        fields.append("host_id = ?")
        values.append(None if changes["host_id"] is None else int(changes["host_id"]))
    if "enabled" in changes and changes["enabled"] is not None:
        fields.append("enabled = ?")
        values.append(1 if changes["enabled"] else 0)
    if "remediation_action" in changes:
        # Un ``None`` explícito desactiva la remediación de la regla.
        fields.append("remediation_action = ?")
        values.append(changes["remediation_action"])
    if not fields:
        return get_rule(rule_id)
    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(rule_id)
    conn = db.connect()
    try:
        conn.execute(f"UPDATE alert_rules SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    # Si la definición cambió, el contador de duración empieza de cero: la
    # nueva regla debe incumplirse durante su propia duración.
    reset_state(rule_id)
    return get_rule(rule_id)


def delete_rule(rule_id: int) -> bool:
    """Elimina una regla y cierra sus eventos abiertos (evita alertas huérfanas)."""
    conn = db.connect()
    try:
        stamp = _now_iso()
        conn.execute(
            "UPDATE alert_events SET status = 'closed', closed_at = ? "
            "WHERE rule_id = ? AND status != 'closed'",
            (stamp, rule_id),
        )
        removed = conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    reset_state(rule_id)
    return bool(removed)


def reset_state(rule_id: int | None = None) -> None:
    """Reinicia el estado en memoria (tests y arranque del servicio).

    Con ``rule_id`` limpia el contador de **todos** los hosts de esa regla.
    """
    if rule_id is None:
        _state.clear()
        return
    for key in [key for key in _state if key[0] == int(rule_id)]:
        _state.pop(key, None)


# ================================ Eventos ===============================
def list_events(
    status: str | None = None,
    limit: int = 50,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Lista eventos de alerta filtrando por estado y fecha de apertura."""
    sql = "SELECT * FROM alert_events"
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if since:
        where.append("opened_at >= ?")
        args.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    conn = db.connect()
    try:
        return [db.row_to_dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def get_event(event_id: int) -> dict[str, Any] | None:
    """Devuelve un evento por su id (``None`` si no existe)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM alert_events WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def open_event_for(rule_id: int, host_id: int | None = None) -> dict[str, Any] | None:
    """Devuelve el evento no cerrado de una regla (dedupe del motor).

    Con ``host_id`` se limita a ese host (los eventos con ``host_id`` nulo son
    del host local, anterior a la migración 5). Sin él, el comportamiento de
    siempre: el evento abierto más reciente de la regla.
    """
    sql = "SELECT * FROM alert_events WHERE rule_id = ? AND status != 'closed' "
    args: list[Any] = [rule_id]
    if host_id is not None:
        sql += "AND (host_id IS NULL OR host_id = ?) "
        args.append(int(host_id))
    sql += "ORDER BY id DESC LIMIT 1"
    conn = db.connect()
    try:
        row = conn.execute(sql, args).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def _insert_event(
    rule: dict[str, Any],
    value: float,
    severity: str,
    message: str,
    suppressed: bool,
    host_id: int = LOCAL_HOST_ID,
) -> dict[str, Any]:
    """Inserta un evento nuevo en estado ``open`` para ese host."""
    stamp = _now_iso()
    conn = db.connect()
    try:
        event_id = db.next_id(conn, "alert_events")
        conn.execute(
            "INSERT INTO alert_events (id, rule_id, host_id, metric, status, severity, "
            "value, threshold, opened_at, suppressed, message) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                int(rule["id"]),
                int(host_id),
                rule["metric"],
                severity,
                float(value),
                float(rule["threshold"]),
                stamp,
                1 if suppressed else 0,
                message,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_event(event_id) or {}


def _close_event(event_id: int, value: float | None = None) -> dict[str, Any] | None:
    """Cierra un evento (recuperación) y devuelve la fila actualizada."""
    stamp = _now_iso()
    conn = db.connect()
    try:
        if value is None:
            conn.execute(
                "UPDATE alert_events SET status = 'closed', closed_at = ? WHERE id = ?",
                (stamp, event_id),
            )
        else:
            conn.execute(
                "UPDATE alert_events SET status = 'closed', closed_at = ?, value = ? "
                "WHERE id = ?",
                (stamp, float(value), event_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get_event(event_id)


def ack_event(event_id: int, actor: str) -> dict[str, Any] | None:
    """Marca un evento como reconocido: deja de notificar y anota quién lo hizo.

    El evento sigue abierto hasta que el motor lo cierre por recuperación; solo
    cambia su estado a ``acked`` (y guarda ``acked_at``/``acked_by``) para que el
    panel muestre la incidencia como ya gestionada. Es idempotente.
    """
    stamp = _now_iso()
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE alert_events SET status = 'acked', acked_at = ?, acked_by = ? "
            "WHERE id = ? AND status = 'open'",
            (stamp, actor, event_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_event(event_id)


def snooze_event(event_id: int, minutes: int) -> dict[str, Any] | None:
    """Aplaza las notificaciones de un evento hasta dentro de N minutos.

    Mientras el snooze esté activo el motor no abre nuevos eventos de esa regla
    (y reinicia su contador de duración), de modo que al expirar el aviso exige
    un incumplimiento continuo en lugar de un pico ya pasado.
    """
    until = (_utcnow() + timedelta(minutes=max(1, int(minutes)))).isoformat(
        timespec="seconds"
    )
    conn = db.connect()
    try:
        changed = conn.execute(
            "UPDATE alert_events SET snooze_until = ? WHERE id = ? AND status != 'closed'",
            (until, event_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    event = get_event(event_id)
    if changed and event is not None and event.get("rule_id") is not None:
        reset_state(int(event["rule_id"]))
    return event


def is_snoozed(rule_id: int, now: float | None = None) -> bool:
    """True si hay un snooze activo (con fecha futura) para la regla.

    El snooze **sobrevive al cierre** del evento: silenciar una regla durante N
    minutos debe seguir vigente aunque el valor se recupere y vuelva a
    incumplir dentro de ese periodo (si no, el aviso saltaría de nuevo nada
    más expirar la incidencia original).
    """
    moment = _utcnow() if now is None else datetime.fromtimestamp(now, tz=timezone.utc)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT snooze_until FROM alert_events WHERE rule_id = ? "
            "AND snooze_until IS NOT NULL ORDER BY id DESC LIMIT 10",
            (rule_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        until = _parse_iso(str(row["snooze_until"]))
        if until is not None and until.timestamp() > moment.timestamp():
            return True
    return False


# ====================== Ventanas de mantenimiento ======================
def active_maintenance(now: float | None = None) -> dict[str, Any] | None:
    """Devuelve la ventana de mantenimiento activa, si la hay.

    Activa = habilitada y ``starts_at <= ahora < ends_at``.
    """
    moment = _utcnow() if now is None else datetime.fromtimestamp(now, tz=timezone.utc)
    stamp = moment.isoformat(timespec="seconds")
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE enabled = 1 "
            "AND starts_at <= ? AND ends_at > ? ORDER BY starts_at DESC LIMIT 1",
            (stamp, stamp),
        ).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def list_maintenance_windows() -> list[dict[str, Any]]:
    """Lista las ventanas de mantenimiento (de más reciente a más antigua)."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM maintenance_windows ORDER BY starts_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [db.row_to_dict(row) for row in rows]


def get_maintenance_window(window_id: int) -> dict[str, Any] | None:
    """Devuelve una ventana de mantenimiento por su id."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE id = ?", (window_id,)
        ).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row) or None


def _window_bounds(starts_at: str, ends_at: str) -> tuple[str, str]:
    """Normaliza y valida el intervalo de una ventana (ISO-8601 UTC)."""
    start = _parse_iso(starts_at)
    end = _parse_iso(ends_at)
    if start is None or end is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Las fechas deben ser ISO-8601 (p. ej. 2026-01-31T20:00:00).",
        )
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "La ventana de mantenimiento debe terminar después de empezar.",
        )
    return (
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def create_maintenance_window(data: dict[str, Any]) -> dict[str, Any]:
    """Crea una ventana de mantenimiento."""
    starts_at, ends_at = _window_bounds(str(data["starts_at"]), str(data["ends_at"]))
    stamp = _now_iso()
    conn = db.connect()
    try:
        window_id = db.next_id(conn, "maintenance_windows")
        conn.execute(
            "INSERT INTO maintenance_windows (id, name, starts_at, ends_at, enabled, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                window_id,
                str(data["name"]),
                starts_at,
                ends_at,
                1 if data.get("enabled", True) else 0,
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_maintenance_window(window_id) or {}


def update_maintenance_window(
    window_id: int, changes: dict[str, Any]
) -> dict[str, Any] | None:
    """Actualiza los campos enviados de una ventana (los ausentes no cambian)."""
    current = get_maintenance_window(window_id)
    if current is None:
        return None
    starts_at, ends_at = _window_bounds(
        str(changes.get("starts_at") or current["starts_at"]),
        str(changes.get("ends_at") or current["ends_at"]),
    )
    fields: list[str] = []
    values: list[Any] = []
    if changes.get("name"):
        fields.append("name = ?")
        values.append(str(changes["name"]))
    if "starts_at" in changes or "ends_at" in changes:
        fields.extend(["starts_at = ?", "ends_at = ?"])
        values.extend([starts_at, ends_at])
    if "enabled" in changes and changes["enabled"] is not None:
        fields.append("enabled = ?")
        values.append(1 if changes["enabled"] else 0)
    if fields:
        values.append(window_id)
        conn = db.connect()
        try:
            conn.execute(
                f"UPDATE maintenance_windows SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()
    return get_maintenance_window(window_id)


def delete_maintenance_window(window_id: int) -> bool:
    """Elimina una ventana de mantenimiento."""
    conn = db.connect()
    try:
        removed = conn.execute(
            "DELETE FROM maintenance_windows WHERE id = ?", (window_id,)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


# ================================ Motor ================================
def evaluate(
    sample: dict[str, Any],
    host_id: int | None = None,
    now: float | None = None,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Evalúa las reglas habilitadas contra la muestra de un host.

    Args:
        sample: diccionario con las métricas de la muestra (claves de
            ``metrics_store.METRIC_COLUMNS``).
        host_id: host al que pertenece la muestra. ``None`` = host local (id 1),
            que es lo que hace el colector de esta máquina. Solo se evalúan las
            reglas cuyo ámbito es ese host o las globales (``host_id`` nulo).
        now: instante Unix de la muestra (por defecto, el actual).

    Returns:
        Lista de ``(accion, evento, regla)`` con ``accion`` = ``"opened"`` o
        ``"closed"``, únicamente para las transiciones que hay que notificar.
    """
    moment = float(now if now is not None else time.time())
    host = int(host_id) if host_id is not None else LOCAL_HOST_ID
    transitions: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    window = active_maintenance(moment)
    suppressed = window is not None

    for rule in list_rules(enabled_only=True):
        if not rule_applies_to(rule, host):
            continue
        metric = str(rule["metric"])
        if metric not in METRIC_COLUMNS:
            logger.warning("Regla {}: métrica desconocida '{}'", rule["id"], metric)
            continue
        value = sample.get(metric)
        if value is None:
            continue  # Sin sensor (p. ej. temp en un PC) no se puede evaluar.
        value = float(value)
        key = (int(rule["id"]), host)
        state = _state.setdefault(key, {"since": None})
        event = open_event_for(int(rule["id"]), host)

        if _breached(rule, value):
            if state["since"] is None:
                state["since"] = moment
            pending_for = moment - float(state["since"])
            if event is not None:
                _refresh_event(int(event["id"]), value, rule)
            if event is None and pending_for >= float(rule.get("duration_s") or 0):
                if is_snoozed(int(rule["id"]), moment):
                    # Snooze activo: no se abre nada y el contador vuelve a cero.
                    state["since"] = None
                    continue
                severity = severity_for(rule, value)
                message = _describe(rule, value)
                new_event = _insert_event(rule, value, severity, message, suppressed, host)
                transitions.append(("opened", new_event, rule))
                state["since"] = None
                audit(
                    "alert.opened",
                    target=f"rule:{rule['id']}",
                    result="ok",
                    details={
                        "metrica": metric,
                        "valor": round(value, 2),
                        "umbral": rule["threshold"],
                        "severidad": severity,
                        "suprimido": suppressed,
                        "host_id": host,
                    },
                )
                logger.warning(
                    "Alerta abierta [{}] {} ({}), host {}", severity, rule["name"], message, host
                )
                _propose_remediation(new_event, rule)
        else:
            state["since"] = None
            if event is not None:
                if _recovered(rule, value):
                    closed = _close_event(int(event["id"]), value)
                    transitions.append(("closed", closed or event, rule))
                    audit(
                        "alert.closed",
                        target=f"rule:{rule['id']}",
                        result="ok",
                        details={
                            "metrica": metric,
                            "valor": round(value, 2),
                            "host_id": host,
                        },
                    )
                    logger.info("Alerta recuperada: {} (host {})", rule["name"], host)
                else:
                    # Dentro de la banda de histéresis: el evento sigue abierto
                    # pero su valor se mantiene al día para el panel.
                    _refresh_event(int(event["id"]), value, rule)
    return transitions


def _propose_remediation(event: dict[str, Any], rule: dict[str, Any]) -> None:
    """Propone (nunca ejecuta) la remediación declarada por la regla.

    Solo actúa si la regla tiene ``remediation_action``; la propuesta queda
    pendiente de confirmación humana. Cualquier fallo se registra pero no impide
    que la alerta se abra: perder una propuesta es mejor que perder la alerta.
    """
    if not rule.get("remediation_action"):
        return
    try:
        from app.modules import remediation  # importación local: evita un ciclo

        remediation.propose(event, rule)
    except Exception as exc:  # noqa: BLE001 - la alerta es lo importante
        logger.warning(
            "No se pudo proponer la remediación de la regla {}: {}",
            rule.get("id"),
            exc,
        )


def _refresh_event(event_id: int, value: float, rule: dict[str, Any]) -> None:
    """Actualiza el valor y la severidad de un evento ya abierto."""
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE alert_events SET value = ?, severity = ?, message = ? WHERE id = ?",
            (
                float(value),
                severity_for(rule, value),
                _describe(rule, value),
                event_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "CRIT_OVERSHOOT",
    "EVENT_SEVERITIES",
    "EVENT_STATUSES",
    "ack_event",
    "active_maintenance",
    "create_maintenance_window",
    "create_rule",
    "delete_maintenance_window",
    "delete_rule",
    "evaluate",
    "get_event",
    "get_maintenance_window",
    "get_rule",
    "is_snoozed",
    "list_events",
    "list_maintenance_windows",
    "list_rules",
    "reset_state",
    "rule_applies_to",
    "severity_for",
    "snooze_event",
    "update_maintenance_window",
    "update_rule",
]