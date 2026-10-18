"""Tests de la fase multi-host: migración, protocolo del agente y API de flota.

Cubren el recorrido completo de un host remoto:

1. La migración 5 conserva el histórico al particionarlo por host y es
   idempotente (se ejecuta sobre una base «antigua» construida a mano).
2. Enrolamiento (token válido / inválido, el secreto se muestra una sola vez y
   queda auditado).
3. Autenticación del agente: token inválido, revocado o de host deshabilitado.
4. Heartbeat: guarda la muestra de **ese** host y refresca su inventario.
5. Aislamiento del histórico por host.
6. Sondeo de comandos (long-poll), marca ``running`` y devolución del resultado.
7. Despacho desde el panel: espera el resultado, 409 si está offline y 504 si
   expira.
8. CRUD de hosts con el host local protegido.
9. Alertas: una regla global se dispara en un host remoto (y el evento lleva su
   ``host_id``); una regla con ámbito no se dispara en otros hosts.

Ejecutar con:
    python -m pytest -q
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import audit, db, fleet
from app.core.db import LOCAL_HOST_ID
from app.main import app
from app.modules import alerts, metrics_store

# ============================== Utilidades ==============================


def _agent_headers(token: str) -> dict[str, str]:
    """Cabecera de autenticación del agente."""
    return {"Authorization": f"Bearer {token}"}


def _panel(client: TestClient, method: str, csrf: str, path: str, **kwargs: Any) -> Any:
    """Atajo para llamar a la API del panel con el token CSRF."""
    kwargs.setdefault("headers", {})["X-CSRF-Token"] = csrf
    return getattr(client, method)(path, **kwargs)


def _enroll(client: TestClient, csrf: str, name: str = "servidor-remoto") -> tuple[int, str]:
    """Enrola un host de pruebas y devuelve ``(host_id, token)``."""
    info = _panel(client, "get", csrf, "/api/hosts/enrollment").json()
    response = _panel(client, "post",
        csrf,
        "/api/agent/enroll",
        json={
            "token": info["token"],
            "name": name,
            "hostname": f"{name}.local",
            "os": "Debian GNU/Linux 12",
            "agent_version": "1.0.0",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return int(body["host_id"]), str(body["token"])


def _heartbeat(token: str, cpu: float = 11.0, **extra: Any) -> dict[str, Any]:
    """Envía un heartbeat de pruebas y devuelve el cuerpo de la respuesta."""
    payload: dict[str, Any] = {
        "metrics": {
            "cpu": cpu,
            "ram": 22.0,
            "swap": 1.0,
            "disk": 33.0,
            "load1": 0.5,
            "temp": 48.0,
        },
        "hostname": "remoto.local",
        "os": "Debian GNU/Linux 12",
        "agent_version": "1.0.0",
    }
    payload.update(extra)
    anonymous = TestClient(app)
    response = anonymous.post("/api/agent/heartbeat", json=payload, headers=_agent_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def clean_alerts() -> Any:
    """Reglas y eventos limpios antes de cada test de alertas."""
    db.migrate()
    alerts.reset_state()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM alert_events")
        conn.execute("DELETE FROM alert_rules")
        conn.execute("DELETE FROM maintenance_windows")
        conn.commit()
    finally:
        conn.close()
    yield
    alerts.reset_state()


@pytest.fixture()
def clean_hosts() -> Any:
    """Elimina los hosts remotos que hayan quedado de otros tests."""
    db.migrate()
    created: list[int] = []
    yield created
    for host_id in created:
        fleet.delete_host(host_id)


def _new_host(clean_hosts: list[int], name: str = "temporal") -> tuple[int, str]:
    """Crea un host con token válido directamente en la capa de datos."""
    created = fleet.create_host(name, hostname=f"{name}.local", os_name="Linux")
    clean_hosts.append(int(created["host"]["id"]))
    fleet.touch(int(created["host"]["id"]))
    return int(created["host"]["id"]), str(created["token"])


# ======================= A. Migración de la flota =======================
def test_migration_partitions_history_and_keeps_rows(tmp_path: Path, monkeypatch: Any) -> None:
    """La migración 5 conserva muestras y agregados y los adjudica al host 1."""
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    legacy = db.connect()
    try:
        legacy.executescript(
            """
            CREATE TABLE metric_samples (
                ts INTEGER PRIMARY KEY, cpu REAL, ram REAL, swap REAL, disk REAL,
                load1 REAL, temp REAL
            );
            CREATE TABLE metric_rollups (
                period TEXT NOT NULL, ts INTEGER NOT NULL, cpu REAL, ram REAL,
                swap REAL, disk REAL, load1 REAL, temp REAL, PRIMARY KEY (period, ts)
            );
            CREATE INDEX idx_rollups_period_ts ON metric_rollups(period, ts);
            CREATE TABLE alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                metric TEXT NOT NULL, op TEXT NOT NULL CHECK (op IN ('>', '<')),
                threshold REAL NOT NULL, clear_threshold REAL,
                duration_s INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE alert_events (
                id INTEGER PRIMARY KEY, rule_id INTEGER, metric TEXT, status TEXT NOT NULL,
                severity TEXT NOT NULL, value REAL, threshold REAL, opened_at TEXT,
                closed_at TEXT, acked_at TEXT, acked_by TEXT, snooze_until TEXT,
                suppressed INTEGER NOT NULL DEFAULT 0, message TEXT
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO metric_samples (ts, cpu, ram, swap, disk, load1, temp) VALUES
                (1700000000, 10.0, 20.0, 0.0, 30.0, 0.5, 45.0),
                (1700000060, 11.0, 21.0, 0.0, 31.0, 0.6, 46.0);
            INSERT INTO metric_rollups (period, ts, cpu, ram, swap, disk, load1, temp) VALUES
                ('1m', 1699999800, 5.0, 6.0, 0.0, 7.0, 0.1, 40.0),
                ('5m', 1699999200, 8.0, 9.0, 0.0, 10.0, 0.2, 41.0),
                ('1h', 1699992000, 3.0, 4.0, 0.0, 5.0, 0.3, 39.0);
            INSERT INTO schema_version VALUES (4, '2024-01-01T00:00:00Z');
            """
        )
        legacy.commit()
    finally:
        legacy.close()

    assert db.migrate() == db.SCHEMA_VERSION

    conn = db.connect()
    try:
        samples = conn.execute("SELECT host_id, ts FROM metric_samples ORDER BY ts").fetchall()
        rollups = conn.execute(
            "SELECT host_id, period, ts FROM metric_rollups ORDER BY period"
        ).fetchall()
        # Ninguna fila se pierde y todas pasan a pertenecer al host local.
        assert len(samples) == 2 and len(rollups) == 3
        assert all(int(row["host_id"]) == LOCAL_HOST_ID for row in samples)
        assert all(int(row["host_id"]) == LOCAL_HOST_ID for row in rollups)
        assert [row["period"] for row in rollups] == ["1h", "1m", "5m"]
        # El host local existe y dos hosts ya pueden compartir segundo.
        local = conn.execute("SELECT name, is_local FROM hosts WHERE id = 1").fetchone()
        assert local["name"] == "Este servidor" and int(local["is_local"]) == 1
        conn.execute("INSERT INTO metric_samples (host_id, ts, cpu) VALUES (1, 1700000120, 1.0)")
        conn.execute("INSERT INTO metric_samples (host_id, ts, cpu) VALUES (2, 1700000120, 2.0)")
        conn.commit()
        same_second = conn.execute(
            "SELECT host_id FROM metric_samples WHERE ts = 1700000120 ORDER BY host_id"
        ).fetchall()
        assert [int(row["host_id"]) for row in same_second] == [1, 2]
    finally:
        conn.close()

    # Idempotente: repetir la migración no cambia nada ni falla.
    assert db.migrate() == db.SCHEMA_VERSION
    conn = db.connect()
    try:
        assert int(conn.execute("SELECT COUNT(*) AS n FROM metric_rollups").fetchone()["n"]) == 3
        # 2 muestras heredadas + las 2 insertadas para dos hosts distintos.
        assert int(conn.execute("SELECT COUNT(*) AS n FROM metric_samples").fetchone()["n"]) == 4
    finally:
        conn.close()


# ========================= B. Protocolo del agente =======================
def test_enroll_requires_the_hub_token(client: TestClient, csrf: str) -> None:
    """Un token de enrolamiento incorrecto no da de alta ningún host."""
    before = len(_panel(client, "get", csrf, "/api/hosts").json()["hosts"])
    denied = _panel(client, "post", csrf, "/api/agent/enroll", json={"token": "token-incorrecto", "name": "x"}
    )
    assert denied.status_code == 403
    assert len(_panel(client, "get", csrf, "/api/hosts").json()["hosts"]) == before

    denied_audit = audit.read_events(limit=50, action="agent.enroll")
    assert any(entry["result"] == "denied" for entry in denied_audit["events"])


def test_enroll_returns_the_token_once_and_audits(client: TestClient, csrf: str, clean_hosts: Any) -> None:
    """El alta devuelve el token en claro y solo se guarda su hash."""
    host_id, token = _enroll(client, csrf, name="nodo-norte")
    clean_hosts.append(host_id)
    assert host_id > LOCAL_HOST_ID
    assert token.startswith(fleet.TOKEN_PREFIX)

    # El hub solo conserva hash y prefijo: el secreto no vuelve a aparecer.
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT token_hash, token_prefix FROM hosts WHERE id = ?", (host_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["token_hash"] == fleet.hash_token(token)
    assert row["token_prefix"] == token[: fleet.PREFIX_LENGTH]
    assert token not in json.dumps(_panel(client, "get", csrf, "/api/hosts").text)

    events = audit.read_events(limit=50, action="agent.enroll")["events"]
    assert any(entry["result"] == "ok" and entry["target"] == f"host:{host_id}" for entry in events)


def test_agent_endpoints_reject_bad_and_revoked_tokens(client: TestClient, csrf: str) -> None:
    """Sin token, con token falso o con el host eliminado, el agente recibe 401."""
    anonymous = TestClient(app)
    assert anonymous.post("/api/agent/heartbeat", json={"metrics": {}}).status_code == 401
    assert (
        anonymous.get("/api/agent/commands", headers=_agent_headers("nxa_inventado")).status_code
        == 401
    )

    host_id, token = _enroll(client, csrf, name="nodo-borrable")
    # Mientras el host existe, su token sirve.
    assert _heartbeat(token)["host_id"] == host_id
    # Deshabilitarlo en el panel revoca el token de inmediato.
    _panel(client, "patch", csrf, f"/api/hosts/{host_id}", json={"enabled": False})
    assert anonymous.post(
        "/api/agent/heartbeat", json={"metrics": {"cpu": 1.0}}, headers=_agent_headers(token)
    ).status_code == 401
    _panel(client, "patch", csrf, f"/api/hosts/{host_id}", json={"enabled": True})

    # Y eliminar el host también (su token y sus datos se van con él).
    assert _panel(client, "delete", csrf, f"/api/hosts/{host_id}").status_code == 200
    assert anonymous.post(
        "/api/agent/heartbeat", json={"metrics": {"cpu": 1.0}}, headers=_agent_headers(token)
    ).status_code == 401


def test_heartbeat_stores_the_sample_and_updates_last_seen(
    client: TestClient, csrf: str, clean_hosts: Any
) -> None:
    """El latido guarda la muestra del host y refresca el inventario."""
    host_id, token = _enroll(client, csrf, name="nodo-latido")
    clean_hosts.append(host_id)
    before = fleet.get_host(host_id)["last_seen_ts"]

    body = _heartbeat(token, cpu=77.5)
    assert body == {"ok": True, "host_id": host_id, "ts": body["ts"]}

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT cpu FROM metric_samples WHERE host_id = ? AND ts = ?", (host_id, body["ts"])
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and float(row["cpu"]) == 77.5

    after = fleet.get_host(host_id)
    assert after["last_seen_ts"] == body["ts"]
    assert before is None or body["ts"] > before
    assert after["status"] == "online" and after["online"] is True
    assert after["agent_version"] == "1.0.0" and after["hostname"] == "remoto.local"

    # Un heartbeat de otro host no aparece en el histórico de este.
    history = _panel(client, "get", csrf, f"/api/hosts/{host_id}/metrics/history?range=1h&metric=cpu").json()
    assert history["host_id"] == host_id

    # El minuto en curso aún no es un bucket cerrado: cuando se cierra, el hub lo
    # consolida y la gráfica del host remoto ya tiene su punto.
    bucket = (body["ts"] // 60) * 60
    metrics_store.build_rollup("1m", bucket, host_id)
    history = _panel(client, "get", csrf, f"/api/hosts/{host_id}/metrics/history?range=1h&metric=cpu").json()
    assert history["points"] >= 1
    assert history["series"]["cpu"][0]["value"] == 77.5

    # Y el consolidado automático solo rehace el bucket cuando toca (idempotente).
    metrics_store.reset_rollup_bookkeeping()
    assert metrics_store.build_rollup_if_due("1m", host_id, now=bucket + 60) == 1
    assert metrics_store.build_rollup_if_due("1m", host_id, now=bucket + 60) == 0


def test_heartbeat_accepts_an_inventory_snapshot(
    client: TestClient, csrf: str, clean_hosts: Any
) -> None:
    """El heartbeat opcionalmente guarda la instantánea del host."""
    host_id, token = _enroll(client, csrf, name="nodo-inventario")
    clean_hosts.append(host_id)
    _heartbeat(token, state={"containers": 3, "services": ["nginx.service"]})

    conn = db.connect()
    try:
        row = conn.execute("SELECT payload_json FROM host_state WHERE host_id = ?", (host_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert json.loads(row["payload_json"])["containers"] == 3


# ============================ C. API de flota ===========================
def test_host_history_is_isolated_per_host(
    client: TestClient, csrf: str, clean_hosts: Any
) -> None:
    """Las series de un host no mezclan las de otro, ni con las del local."""
    host_id, token = _enroll(client, csrf, name="nodo-historico")
    clean_hosts.append(host_id)

    now = int(time.time())
    local_sample = {"cpu": 1.0, "ram": 2.0, "swap": 0.0, "disk": 3.0, "load1": 0.1, "temp": 30.0}
    remote_sample = {**local_sample, "cpu": 91.0}
    # Ambas muestras caen en el mismo minuto cerrado: es el bucket que se
    # consolida y el que devuelve el rango de una hora (periodo 1m).
    bucket = metrics_store.last_complete_bucket("1m", now)
    metrics_store.insert_sample(bucket + 5, local_sample, LOCAL_HOST_ID)
    metrics_store.insert_sample(bucket + 15, remote_sample, host_id)
    metrics_store.build_rollup("1m", bucket, LOCAL_HOST_ID)
    metrics_store.build_rollup("1m", bucket, host_id)

    remote = _panel(client, "get", csrf, f"/api/hosts/{host_id}/metrics/history?range=1h&metric=cpu"
    ).json()
    local = _panel(client, "get", csrf, "/api/metrics-history?range=1h&metric=cpu").json()
    assert remote["host_id"] == host_id and local["host_id"] == LOCAL_HOST_ID
    remote_values = {point["value"] for point in remote["series"]["cpu"]}
    assert remote_values == {91.0}
    assert all(point["value"] != 91.0 for point in local["series"]["cpu"] if point["value"])

    # El endpoint antiguo acepta host_id explícito y no rompe sin él.
    explicit = _panel(client, "get", csrf, f"/api/metrics-history?range=1h&metric=cpu&host_id={host_id}"
    ).json()
    assert explicit["host_id"] == host_id
    assert explicit["series"]["cpu"] == remote["series"]["cpu"]


def test_hosts_crud_and_local_host_protection(
    client: TestClient, csrf: str, clean_hosts: Any
) -> None:
    """Alta, edición, borrado y la protección del host local."""
    listing = _panel(client, "get", csrf, "/api/hosts").json()
    assert listing["count"] >= 1
    assert any(host["id"] == LOCAL_HOST_ID and host["is_local"] == 1 for host in listing["hosts"])

    host_id, token = _enroll(client, csrf, name="nodo-editable")
    clean_hosts.append(host_id)

    patched = _panel(client, "patch", csrf, f"/api/hosts/{host_id}", json={"name": "renombrado", "notes": "sala 1"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "renombrado" and patched.json()["notes"] == "sala 1"

    # El host local no se toca ni se borra.
    assert _panel(client, "patch", csrf, f"/api/hosts/{LOCAL_HOST_ID}", json={"enabled": False}).status_code == 409
    assert _panel(client, "delete", csrf, f"/api/hosts/{LOCAL_HOST_ID}").status_code == 409
    assert fleet.get_host(LOCAL_HOST_ID) is not None

    # Deshabilitar un remoto lo saca de la lista de en línea sin borrarlo.
    _panel(client, "patch", csrf, f"/api/hosts/{host_id}", json={"enabled": False})
    disabled = _panel(client, "get", csrf, "/api/hosts").json()
    entry = next(host for host in disabled["hosts"] if host["id"] == host_id)
    assert entry["status"] == "disabled" and entry["enabled"] == 0

    assert _panel(client, "delete", csrf, f"/api/hosts/{host_id}").status_code == 200
    assert fleet.get_host(host_id) is None
    assert _panel(client, "delete", csrf, f"/api/hosts/{host_id}").status_code == 404
    assert token  # el token ya no sirve (comprobado en el test de revocación)


def test_enrollment_endpoint_is_admin_only(client: TestClient, csrf: str) -> None:
    """El token de enrolamiento es un secreto: solo lo ve el panel."""
    anonymous = TestClient(app)
    assert anonymous.get("/api/hosts/enrollment").status_code == 401
    assert anonymous.get("/api/hosts").status_code == 401

    info = _panel(client, "get", csrf, "/api/hosts/enrollment")
    assert info.status_code == 200
    body = info.json()
    assert body["token"]
    assert body["source"] in ("entorno", "generado")
    assert "agent.py enroll" in body["command"]
    assert "secreto" in body["warning"].lower()


def test_dispatch_refuses_an_offline_host(client: TestClient, csrf: str, clean_hosts: Any) -> None:
    """No se encolan comandos a un host sin latido (409)."""
    host_id, _token = _enroll(client, csrf, name="nodo-apagado")
    clean_hosts.append(host_id)

    response = _panel(client, "post", csrf, f"/api/hosts/{host_id}/command", json={"tool": "metrics", "args": {}}
    )
    assert response.status_code == 409
    assert "desconectado" in response.json()["detail"].lower()


def test_dispatch_times_out_and_expires_the_command(
    client: TestClient, csrf: str, clean_hosts: Any
) -> None:
    """Sin respuesta del agente, el comando caduca (504) y queda expirado."""
    host_id, token = _enroll(client, csrf, name="nodo-mudo")
    clean_hosts.append(host_id)
    _heartbeat(token)

    started = time.monotonic()
    response = _panel(client, "post",
        csrf,
        f"/api/hosts/{host_id}/command",
        json={"tool": "metrics", "args": {}, "timeout_s": 1},
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 504
    assert 0.8 <= elapsed < 8

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM commands WHERE host_id = ? ORDER BY id DESC LIMIT 1", (host_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "expired"


# ======================= D. Sondeo y resultados =========================
def test_agent_poll_takes_a_command_and_marks_it_running(clean_hosts: Any) -> None:
    """El sondeo entrega el comando más antiguo y lo marca «running»."""
    host_id, token = _new_host(clean_hosts, name="nodo-sondeo")
    fleet.create_command(host_id, "metrics", {}, actor="admin")
    fleet.create_command(host_id, "services.list", {}, actor="admin")

    anonymous = TestClient(app)
    first = anonymous.get("/api/agent/commands?wait=0", headers=_agent_headers(token))
    assert first.status_code == 200
    command = first.json()
    assert command["tool"] == "metrics"
    assert fleet.get_command(command["id"])["status"] == "running"
    assert fleet.get_command(command["id"])["sent_at"]

    # El segundo sondeo entrega el siguiente comando (el primero ya está tomado).
    second = anonymous.get("/api/agent/commands?wait=0", headers=_agent_headers(token))
    assert second.json()["tool"] == "services.list"

    # Sin nada pendiente responde 204 (el long-poll ended al agotar la espera).
    fleet.finish_command(command["id"], "done", {"ok": True}, host_id=host_id)
    fleet.finish_command(second.json()["id"], "done", {"ok": True}, host_id=host_id)
    empty = anonymous.get("/api/agent/commands?wait=0", headers=_agent_headers(token))
    assert empty.status_code == 204
    assert host_id > 0


def test_agent_can_only_close_its_own_commands(clean_hosts: Any) -> None:
    """Un token de un host no puede escribir el resultado de otro host."""
    first_id, first_token = _new_host(clean_hosts, name="nodo-uno")
    second_id, _second_token = _new_host(clean_hosts, name="nodo-dos")
    foreign = fleet.create_command(second_id, "metrics", {}, actor="admin")
    fleet.next_pending_command(second_id)

    anonymous = TestClient(app)
    response = anonymous.post(
        f"/api/agent/commands/{foreign['id']}/result",
        json={"status": "done", "result": {"hack": True}},
        headers=_agent_headers(first_token),
    )
    assert response.status_code == 404
    assert fleet.get_command(foreign["id"])["status"] == "running"


def test_dispatch_waits_for_the_agent_result(client: TestClient, csrf: str, clean_hosts: Any) -> None:
    """El panel encola, el agente sondea, ejecuta y devuelve; el panel responde."""
    host_id, token = _enroll(client, csrf, name="nodo-despacho")
    clean_hosts.append(host_id)
    _heartbeat(token)
    stop = threading.Event()
    answered: dict[str, Any] = {}

    def fake_agent() -> None:
        """Simula al agente: sondea, responde y se detiene."""
        agent = TestClient(app)
        headers = _agent_headers(token)
        agent.post("/api/agent/heartbeat", json={
            "metrics": {"cpu": 5.0}, "hostname": "remoto.local", "os": "Linux",
            "agent_version": "1.0.0",
        }, headers=headers)
        while not stop.is_set():
            poll = agent.get("/api/agent/commands?wait=20", headers=headers)
            if poll.status_code != 200:
                continue
            command = poll.json()
            answered["tool"] = command["tool"]
            answered["id"] = command["id"]
            agent.post(
                f"/api/agent/commands/{command['id']}/result",
                json={"status": "done", "result": {"contenedores": 2, "ok": True}},
                headers=headers,
            )
            return

    worker = threading.Thread(target=fake_agent, daemon=True)
    worker.start()
    try:
        response = _panel(client, "post",
            csrf,
            f"/api/hosts/{host_id}/command",
            json={"tool": "docker.list", "args": {}, "timeout_s": 30},
        )
    finally:
        stop.set()
        worker.join(timeout=10)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["tool"] == "docker.list"
    assert body["result"] == {"contenedores": 2, "ok": True}
    assert body["error"] is None
    assert answered["tool"] == "docker.list"
    assert fleet.get_command(body["command_id"])["status"] == "done"

    # El error del agente se propaga como estado «error», no como fallo HTTP.
    stop.clear()

    def failing_agent() -> None:
        agent = TestClient(app)
        headers = _agent_headers(token)
        while not stop.is_set():
            poll = agent.get("/api/agent/commands?wait=20", headers=headers)
            if poll.status_code != 200:
                continue
            agent.post(
                f"/api/agent/commands/{poll.json()['id']}/result",
                json={"status": "error", "error": "socket de Docker no disponible"},
                headers=headers,
            )
            return

    worker = threading.Thread(target=failing_agent, daemon=True)
    worker.start()
    try:
        failed = _panel(client, "post",
            csrf,
            f"/api/hosts/{host_id}/command",
            json={"tool": "docker.list", "args": {}, "timeout_s": 30},
        )
    finally:
        stop.set()
        worker.join(timeout=10)

    assert failed.status_code == 200
    assert failed.json()["status"] == "error"
    assert "Docker" in failed.json()["error"]


# ====================== E. Alertas por host =============================
def test_global_rule_fires_on_a_remote_host(clean_alerts: Any) -> None:
    """Una regla global (host_id nulo) se dispara en cualquier host."""
    rule = alerts.create_rule(
        {
            "name": "CPU alta global",
            "metric": "cpu",
            "op": ">",
            "threshold": 90.0,
            "duration_s": 0,
            "enabled": True,
        }
    )
    assert rule["host_id"] is None

    now = time.time()
    # El host local no incumple: no debe abrir nada.
    assert alerts.evaluate({"cpu": 10.0}, LOCAL_HOST_ID, now=now) == []
    # El host remoto sí: se abre un evento con su host_id.
    transitions = alerts.evaluate({"cpu": 97.0}, 7, now=now + 5)
    assert [action for action, _e, _r in transitions] == ["opened"]
    event = transitions[0][1]
    assert event["host_id"] == 7
    assert event["rule_id"] == rule["id"]
    # El host local sigue sin eventos propios.
    assert alerts.open_event_for(rule["id"], LOCAL_HOST_ID) is None


def test_scoped_rule_only_fires_on_its_host(clean_alerts: Any) -> None:
    """Una regla con host_id no se evalúa en los demás hosts."""
    scoped = alerts.create_rule(
        {
            "name": "RAM alta en el nodo 7",
            "metric": "ram",
            "op": ">",
            "threshold": 80.0,
            "duration_s": 0,
            "enabled": True,
            "host_id": 7,
        }
    )
    now = time.time()
    assert alerts.evaluate({"ram": 95.0}, LOCAL_HOST_ID, now=now) == []
    assert alerts.evaluate({"ram": 95.0}, 9, now=now + 5) == []
    assert alerts.list_events() == []

    transitions = alerts.evaluate({"ram": 95.0}, 7, now=now + 10)
    assert [action for action, _e, _r in transitions] == ["opened"]
    assert transitions[0][1]["host_id"] == 7

    # Y el dedupe es por host: el local recupera, el remoto sigue abierto.
    closed = alerts.evaluate({"ram": 10.0}, LOCAL_HOST_ID, now=now + 15)
    assert [action for action, _e, _r in closed] == []
    assert alerts.open_event_for(scoped["id"], 7) is not None


def test_heartbeat_triggers_alerts_for_that_host(
    client: TestClient, csrf: str, clean_hosts: Any, clean_alerts: Any
) -> None:
    """El latido de un host remoto abre (y cierra) eventos con su host_id."""
    host_id, token = _enroll(client, csrf, name="nodo-alertas")
    clean_hosts.append(host_id)
    alerts.create_rule(
        {
            "name": "CPU del remoto",
            "metric": "cpu",
            "op": ">",
            "threshold": 90.0,
            "duration_s": 0,
            "enabled": True,
        }
    )

    _heartbeat(token, cpu=99.0)
    opened = alerts.list_events(status="open")
    assert len(opened) == 1 and int(opened[0]["host_id"]) == host_id

    _heartbeat(token, cpu=5.0)
    assert alerts.list_events(status="open") == []
    assert int(alerts.list_events()[0]["host_id"]) == host_id


# ======================== F. Compatibilidad local =======================
def test_local_mode_still_works(client: TestClient, csrf: str) -> None:
    """Sin agentes, el panel se comporta exactamente como antes."""
    hosts = _panel(client, "get", csrf, "/api/hosts").json()
    local = next(host for host in hosts["hosts"] if host["id"] == LOCAL_HOST_ID)
    assert local["name"] == "Este servidor"
    assert local["is_local"] == 1

    metrics = _panel(client, "get", csrf, "/api/metrics-history?range=1h&metric=cpu,ram").json()
    assert metrics["host_id"] == LOCAL_HOST_ID
    assert set(metrics["series"]) == {"cpu", "ram"}
    assert _panel(client, "get", csrf, "/api/metrics-export.csv?range=1h").status_code == 200


def test_schema_reports_the_fleet_tables() -> None:
    """La migración 5 deja las tres tablas de flota en el esquema."""
    db.migrate()
    assert {"hosts", "commands", "host_state"} <= db.table_names()
    conn = db.connect()
    try:
        assert sqlite3.Row is not None
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(metric_rollups)")}
        assert "host_id" in columns
        rules = {row["name"] for row in conn.execute("PRAGMA table_info(alert_rules)")}
        assert "host_id" in rules
    finally:
        conn.close()