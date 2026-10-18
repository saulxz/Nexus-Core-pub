"""Tests del motor de alertas, las notificaciones y sus endpoints.

Ejecutar con:
    python -m pytest -q
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import app
from app.modules import alerts, notifications


@pytest.fixture(autouse=True)
def _clean_alerts() -> Any:
    """Esquema migrado y estado de alertas limpio antes de cada test."""
    db.migrate()
    alerts.reset_state()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM alert_events")
        conn.execute("DELETE FROM alert_rules")
        conn.execute("DELETE FROM maintenance_windows")
        conn.execute("DELETE FROM notification_channels")
        conn.commit()
    finally:
        conn.close()
    yield
    alerts.reset_state()


def _sample(cpu: float = 10.0, ram: float = 20.0) -> dict[str, float | None]:
    """Muestra de prueba completa."""
    return {"cpu": cpu, "ram": ram, "swap": 0.0, "disk": 42.0, "load1": 0.4, "temp": 55.0}


def _rule(**overrides: Any) -> dict[str, Any]:
    """Regla de prueba con valores por defecto sensatos."""
    data: dict[str, Any] = {
        "name": "prueba",
        "metric": "cpu",
        "op": ">",
        "threshold": 80.0,
        "clear_threshold": None,
        "duration_s": 0,
        "enabled": True,
    }
    data.update(overrides)
    return alerts.create_rule(data)


# ============================ Estado del motor ============================
def test_breach_opens_event_immediately_without_duration() -> None:
    """Sin duración exigida, la primera muestra que incumple abre el evento."""
    rule = _rule()
    transitions = alerts.evaluate(_sample(cpu=95.0), now=time.time())

    assert [action for action, _event, _rule in transitions] == ["opened"]
    event = transitions[0][1]
    assert event["status"] == "open"
    assert event["rule_id"] == rule["id"]
    assert event["metric"] == "cpu"
    assert event["value"] == 95.0


def test_duration_defers_the_event() -> None:
    """La duración exige incumplimiento continuo antes de alertar."""
    rule = _rule(duration_s=60)
    start = time.time()

    assert alerts.evaluate(_sample(cpu=95.0), now=start) == []
    assert alerts.evaluate(_sample(cpu=50.0), now=start + 10) == []  # reinicia el contador
    assert alerts.evaluate(_sample(cpu=95.0), now=start + 20) == []
    assert alerts.evaluate(_sample(cpu=95.0), now=start + 79) == []

    transitions = alerts.evaluate(_sample(cpu=95.0), now=start + 80)
    assert [action for action, _e, _r in transitions] == ["opened"]
    assert rule["duration_s"] == 60


def test_only_one_open_event_per_rule() -> None:
    """Mientras el evento está abierto no se crea otro (dedupe)."""
    _rule()
    now = time.time()
    alerts.evaluate(_sample(cpu=95.0), now=now)
    assert alerts.evaluate(_sample(cpu=97.0), now=now + 5) == []

    open_events = alerts.list_events(status="open")
    assert len(open_events) == 1
    # El valor del evento se mantiene al día aunque no se re-notifique.
    assert open_events[0]["value"] == 97.0


def test_hysteresis_delays_the_close() -> None:
    """Con clear_threshold, el evento sigue abierto en la banda intermedia."""
    _rule(threshold=80.0, clear_threshold=60.0)
    now = time.time()
    alerts.evaluate(_sample(cpu=90.0), now=now)

    # Sigue incumpliendo (> 80): no se cierra.
    assert alerts.evaluate(_sample(cpu=70.0), now=now + 5) == []
    assert alerts.list_events(status="open")[0]["value"] == 70.0

    # Baja del umbral de recuperación: se cierra.
    transitions = alerts.evaluate(_sample(cpu=55.0), now=now + 10)
    assert [action for action, _e, _r in transitions] == ["closed"]
    assert alerts.list_events(status="open") == []


def test_close_threshold_defaults_to_threshold() -> None:
    """Sin clear_threshold, recuperar significa volver al otro lado del umbral."""
    _rule(threshold=80.0)
    now = time.time()
    alerts.evaluate(_sample(cpu=90.0), now=now)
    transitions = alerts.evaluate(_sample(cpu=75.0), now=now + 5)
    assert [action for action, _e, _r in transitions] == ["closed"]


def test_recovery_reopens_a_new_event() -> None:
    """Tras la recuperación, un nuevo incumplimiento abre otro evento."""
    _rule()
    now = time.time()
    alerts.evaluate(_sample(cpu=95.0), now=now)
    alerts.evaluate(_sample(cpu=10.0), now=now + 5)
    transitions = alerts.evaluate(_sample(cpu=99.0), now=now + 10)
    assert [action for action, _e, _r in transitions] == ["opened"]
    assert len(alerts.list_events()) == 2


def test_severity_rules() -> None:
    """Sin clear_threshold la alerta es crítica; con banda, avisa o escala."""
    hard = _rule(threshold=85.0, clear_threshold=None)
    soft = _rule(threshold=80.0, clear_threshold=60.0)
    assert alerts.severity_for(hard, 86.0) == "crit"

    # Dentro de la banda de histéresis (10 % de desvío) solo avisa…
    assert alerts.severity_for(soft, 88.0) == "warn"
    # …pero si se desvía un 25 % o más, escala a crítica.
    assert alerts.severity_for(soft, 105.0) == "crit"


def test_metric_without_sensor_is_skipped() -> None:
    """Sin sensor (temp=None) la regla no dispara ni falla."""
    _rule(metric="temp", threshold=50.0)
    sample = _sample()
    sample["temp"] = None
    assert alerts.evaluate(sample, now=time.time()) == []


def test_disabled_rule_is_ignored() -> None:
    """Una regla deshabilitada no se evalúa."""
    _rule(enabled=False)
    assert alerts.evaluate(_sample(cpu=99.0), now=time.time()) == []


def test_updating_a_rule_resets_the_timer() -> None:
    """Cambiar la definición de la regla reinicia el contador de duración."""
    rule = _rule(duration_s=60)
    start = time.time()
    alerts.evaluate(_sample(cpu=95.0), now=start)
    alerts.update_rule(rule["id"], {"threshold": 90.0})
    assert alerts.evaluate(_sample(cpu=95.0), now=start + 30) == []
    assert [a for a, _e, _r in alerts.evaluate(_sample(cpu=95.0), now=start + 91)] == ["opened"]


# ========================== Mantenimiento ==========================
def test_maintenance_window_suppresses_notification() -> None:
    """En ventana de mantenimiento el evento se registra pero no se notifica."""
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    window = alerts.create_maintenance_window(
        {
            "name": "Actualización del SO",
            "starts_at": start.isoformat(timespec="seconds"),
            "ends_at": (start + timedelta(hours=1)).isoformat(timespec="seconds"),
        }
    )
    assert alerts.active_maintenance() is not None

    _rule()
    transitions = alerts.evaluate(_sample(cpu=99.0), now=time.time())

    assert [action for action, _e, _r in transitions] == ["opened"]
    assert transitions[0][1]["suppressed"] == 1
    # Sin ventana activa el evento se vuelve a notificar con normalidad.
    alerts.delete_maintenance_window(int(window["id"]))
    alerts.reset_state()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM alert_events")
        conn.commit()
    finally:
        conn.close()
    second = alerts.evaluate(_sample(cpu=99.0), now=time.time())
    assert second[0][1]["suppressed"] == 0


def test_dispatch_skips_suppressed_events() -> None:
    """El despacho no envía nada si el evento está suprimido."""
    event = {"id": 1, "metric": "cpu", "severity": "warn", "suppressed": 1, "value": 99.0}
    sent = asyncio.run(notifications.dispatch_event("opened", event, {"name": "prueba"}))
    assert sent == 0


# ====================== Guardas y estado de reglas ======================
def test_delete_rule_closes_its_events() -> None:
    """Al borrar una regla no quedan eventos abiertos huérfanos."""
    rule = _rule()
    alerts.evaluate(_sample(cpu=99.0), now=time.time())
    assert alerts.delete_rule(rule["id"]) is True
    assert alerts.list_events(status="open") == []
    assert alerts.delete_rule(rule["id"]) is False


def test_maintenance_window_rejects_inverted_range() -> None:
    """Una ventana que termina antes de empezar se rechaza con 400."""
    from fastapi import HTTPException

    now = datetime.now(timezone.utc)
    with pytest.raises(HTTPException) as error:
        alerts.create_maintenance_window(
            {
                "name": "imposible",
                "starts_at": now.isoformat(timespec="seconds"),
                "ends_at": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            }
        )
    assert error.value.status_code == 400


# ============================== SSRF guard ==============================
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/hook",
        "http://localhost/admin",
        "http://10.0.0.5/hook",
        "http://192.168.1.10:9000/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:8080/hook",
        "http://0.0.0.0/hook",
        "file:///etc/passwd",
    ],
)
def test_ssrf_guard_blocks_internal_targets(url: str) -> None:
    """Loopback, redes privadas, link-local y esquemas no HTTP se rechazan."""
    with pytest.raises(notifications.SSRFError):
        notifications.check_webhook_url(url)


def test_ssrf_guard_allows_public_ip() -> None:
    """Una IP pública se acepta sin necesidad de resolver nombres."""
    assert notifications.check_webhook_url("https://93.184.216.34/hook")


def test_webhook_channel_rejects_private_url() -> None:
    """No se puede crear un canal webhook apuntando a la red interna."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        notifications.create_channel(
            "interno", "webhook", {"url": "http://192.168.1.1/hook"}
        )
    assert error.value.status_code == 400


# ========================== Endpoint de reglas ==========================
def test_alert_rules_api_crud(client: TestClient, csrf: str) -> None:
    """Alta, consulta, edición y borrado de reglas desde la API."""
    created = client.post(
        "/api/alert-rules",
        json={
            "name": "CPU alta",
            "metric": "cpu",
            "op": ">",
            "threshold": 90,
            "clear_threshold": 70,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["metric"] == "cpu" and rule["clear_threshold"] == 70.0

    listed = client.get("/api/alert-rules")
    assert listed.status_code == 200
    assert any(item["id"] == rule["id"] for item in listed.json()["rules"])

    patched = client.patch(
        f"/api/alert-rules/{rule['id']}",
        json={"threshold": 95},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched.status_code == 200
    assert patched.json()["threshold"] == 95.0
    # El clear_threshold no enviado se conserva.
    assert patched.json()["clear_threshold"] == 70.0

    deleted = client.delete(f"/api/alert-rules/{rule['id']}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200 and deleted.json()["success"] is True
    assert client.patch(
        f"/api/alert-rules/{rule['id']}", json={}, headers={"X-CSRF-Token": csrf}
    ).status_code == 404


def test_alert_rule_rejects_wrong_hysteresis(client: TestClient, csrf: str) -> None:
    """Una banda de histéresis incoherente con el operador se rechaza (422)."""
    response = client.post(
        "/api/alert-rules",
        json={"name": "malo", "metric": "cpu", "op": ">", "threshold": 50, "clear_threshold": 80},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_alert_events_ack_and_snooze(client: TestClient, csrf: str) -> None:
    """ack cambia el estado y anota el usuario; snooze fija la fecha de fin."""
    _rule()
    alerts.evaluate(_sample(cpu=99.0), now=time.time())
    event_id = alerts.list_events(status="open")[0]["id"]

    acked = client.post(f"/api/alert-events/{event_id}/ack", headers={"X-CSRF-Token": csrf})
    assert acked.status_code == 200
    assert acked.json()["status"] == "acked"
    assert acked.json()["acked_by"] == "admin"

    snoozed = client.post(
        f"/api/alert-events/{event_id}/snooze",
        json={"minutes": 30},
        headers={"X-CSRF-Token": csrf},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["snooze_until"] > snoozed.json()["opened_at"]

    # Un evento reconnaissance sigue abierto hasta la recuperación.
    assert alerts.get_event(event_id)["status"] == "acked"


def test_snooze_silences_new_events(client: TestClient, csrf: str) -> None:
    """Con snooze activo la regla no abre un evento nuevo al reincidir."""
    rule = _rule()
    now = time.time()
    alerts.evaluate(_sample(cpu=99.0), now=now)
    event_id = alerts.list_events(status="open")[0]["id"]
    alerts.snooze_event(event_id, 60)

    # Cierra y vuelve a incumplir: el motor no abre nada mientras dure el snooze.
    alerts.evaluate(_sample(cpu=10.0), now=now + 5)
    assert alerts.evaluate(_sample(cpu=99.0), now=now + 10) == []
    assert alerts.open_event_for(rule["id"]) is None


def test_alert_events_filter_by_status(client: TestClient) -> None:
    """El listado de eventos acepta el filtro de estado y rechaza otros valores."""
    _rule()
    alerts.evaluate(_sample(cpu=99.0), now=time.time())
    response = client.get("/api/alert-events?status=open&limit=10")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert client.get("/api/alert-events?status=inventado").status_code == 422


def test_alert_endpoints_require_authentication() -> None:
    """Sin sesión, los endpoints nuevos responden 401."""
    anonymous = TestClient(app)
    for path in (
        "/api/alert-rules",
        "/api/alert-events",
        "/api/notification-channels",
        "/api/maintenance-windows",
        "/api/metrics-history",
        "/api/audit",
    ):
        assert anonymous.get(path).status_code == 401, path


# ======================= API de canales y ventanas =======================
def test_notification_channels_api_and_masking(client: TestClient, csrf: str) -> None:
    """El canal se crea, el secreto se enmascara y el webhook privado se rechaza."""
    created = client.post(
        "/api/notification-channels",
        json={
            "name": "Telegram del admin",
            "kind": "telegram",
            "config": {"token": "123456789:ABCdefGHIJKLMNOPqrstUV", "chat_id": "-1001"},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # El token nunca vuelve en claro, pero se ve que está configurado.
    assert body["config"]["token"].startswith("•")
    assert "ABCdefGHIJKLMNOPqrstUV" not in created.text
    assert body["config"]["chat_id"] == "-1001"

    listed = client.get("/api/notification-channels")
    assert listed.json()["count"] == 1

    denied = client.post(
        "/api/notification-channels",
        json={"name": "interno", "kind": "webhook", "config": {"url": "http://10.1.1.1/hook"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 400

    removed = client.delete(
        f"/api/notification-channels/{body['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert removed.status_code == 200


def test_maintenance_windows_api(client: TestClient, csrf: str) -> None:
    """Alta y borrado de una ventana de mantenimiento."""
    now = datetime.now(timezone.utc)
    created = client.post(
        "/api/maintenance-windows",
        json={
            "name": "Actualización de paquetes",
            "starts_at": now.isoformat(timespec="seconds"),
            "ends_at": (now + timedelta(hours=2)).isoformat(timespec="seconds"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    window = created.json()
    assert alerts.active_maintenance()["id"] == window["id"]

    assert client.delete(
        f"/api/maintenance-windows/{window['id']}", headers={"X-CSRF-Token": csrf}
    ).status_code == 200
    assert client.delete(
        f"/api/maintenance-windows/{window['id']}", headers={"X-CSRF-Token": csrf}
    ).status_code == 404


def test_maintenance_window_rejects_bad_dates(client: TestClient, csrf: str) -> None:
    """Fechas no ISO o una ventana invertida devuelven 422."""
    response = client.post(
        "/api/maintenance-windows",
        json={"name": "malo", "starts_at": "ayer", "ends_at": "mañana"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


# ========================== API de histórico ==========================
def test_metrics_history_api(client: TestClient) -> None:
    """El histórico responde con el rango, el nivel y las series pedidas."""
    response = client.get("/api/metrics-history?range=24h&metric=cpu,temp")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == "24h"
    assert body["period"] == "1m"
    assert set(body["series"].keys()) == {"cpu", "temp"}
    assert body["points"] <= 1000


def test_metrics_history_rejects_unknown_metric(client: TestClient) -> None:
    """Una métrica inexistente se rechaza con 400."""
    response = client.get("/api/metrics-history?metric=cpu,gpu")
    assert response.status_code == 400


def test_metrics_export_csv(client: TestClient) -> None:
    """La exportación CSV se descarga como fichero con cabecera."""
    response = client.get("/api/metrics-export.csv?range=1h")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0].startswith("timestamp,iso,")


def test_audit_api_paginates(client: TestClient, csrf: str) -> None:
    """El endpoint de auditoría pagina y expone el estado de la cadena."""
    client.post(
        "/api/alert-rules",
        json={"name": "auditada", "metric": "ram", "op": ">", "threshold": 70},
        headers={"X-CSRF-Token": csrf},
    )
    response = client.get("/api/audit?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] <= 5
    assert body["chain"]["valid"] is True
    assert any(entry["action"] == "alert.rule.created" for entry in body["events"])

    filtered = client.get("/api/audit?action=alert.rule.created")
    assert all(entry["action"] == "alert.rule.created" for entry in filtered.json()["events"])


# ============================ Colector ============================
def test_collector_tick_saves_sample_and_notifies() -> None:
    """Un tick guarda la muestra y notifica las transiciones que.evaluate devuelve."""
    from app.modules.metrics_collector import MetricsCollector

    rule = _rule()
    sent: list[tuple[str, int]] = []

    async def notify(action: str, event: dict[str, Any], _rule: dict[str, Any]) -> None:
        sent.append((action, int(event["rule_id"])))

    collector = MetricsCollector(sample_fn=lambda: _sample(cpu=99.0), notify=notify)
    asyncio.run(collector.tick())

    assert sent == [("opened", rule["id"])]
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM metric_samples").fetchone()["n"] >= 1
    finally:
        conn.close()


def test_collector_stops_without_leaking_tasks() -> None:
    """``stop()`` termina el bucle y la tarea queda finalized."""

    async def scenario() -> None:
        from app.modules.metrics_collector import MetricsCollector

        collector = MetricsCollector(sample_fn=lambda: _sample())
        task = asyncio.create_task(collector.run())
        await asyncio.sleep(0.2)
        await collector.stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())