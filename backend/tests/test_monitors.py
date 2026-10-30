"""Tests de monitores de disponibilidad, estado público y exportador Prometheus.

Ejecutar con:
    python -m pytest -q

Los heartbeats se insertan directamente (sintéticos) para poder comprobar el
cálculo de uptime sin esperar a los intervalos reales; los comprobantes se
prueban con un cliente httpx simulado (sin red) y contra un socket local real.
El comprobador de segundo plano está desactivado en los tests (ver
``tests/conftest.py``), de modo que ningún destino ficticio recibe tráfico.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.config import get_settings
from app.main import app
from app.modules import alerts, metrics_store, monitor_checker, monitors, prometheus


@pytest.fixture(autouse=True)
def _clean_monitors() -> Any:
    """Esquema migrado y tablas de monitores vacías antes de cada test."""
    db.migrate()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM heartbeats")
        conn.execute("DELETE FROM monitors")
        conn.commit()
    finally:
        conn.close()
    yield


def _monitor(**overrides: Any) -> dict[str, Any]:
    """Crea un monitor de prueba con valores por defecto sensatos."""
    data: dict[str, Any] = {
        "name": "Web principal",
        "kind": "http",
        "target": "http://localhost:8000/health",
        "interval_s": 60,
        "timeout_s": 5,
        "enabled": True,
    }
    data.update(overrides)
    return monitors.create_monitor(data)


def _beat(
    monitor_id: int, *, up: bool, age_s: float = 0.0, latency: float = 12.5
) -> int:
    """Inserta un heartbeat sintético ``age_s`` segundos en el pasado."""
    return monitors.insert_heartbeat(
        monitor_id,
        int(time.time() - age_s),
        up,
        latency,
        200 if up else 500,
        f"HTTP {200 if up else 500}",
    )


def _insert_alert(alert_id: int, severity: str, status: str = "open") -> None:
    """Inserta un evento de alerta para contar los abiertos por severidad."""
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO alert_events (id, metric, status, severity, value, threshold) "
            "VALUES (?, 'cpu', ?, ?, 99.0, 80.0)",
            (alert_id, status, severity),
        )
        conn.commit()
    finally:
        conn.close()


# ============================ Parseo de destinos ============================
def test_parse_http_target_keeps_host_port_and_url() -> None:
    """Una URL http(s) se normaliza a host, puerto y URL utilizable."""
    parsed = monitors.parse_target("http", "http://192.168.1.10:8080/health?x=1")
    assert parsed.host == "192.168.1.10"
    assert parsed.port == 8080
    assert parsed.url == "http://192.168.1.10:8080/health?x=1"

    # Sin puerto explícito manda el de la URL; el campo 'port' tiene prioridad.
    assert monitors.parse_target("http", "https://example.org").port == 443
    assert monitors.parse_target("http", "https://example.org", 8443).port == 8443
    # Un puerto explícito sustituye al de la URL también en la petición real.
    override = monitors.parse_target("http", "http://example.org:8080/health", 9000)
    assert override.port == 9000
    assert override.url == "http://example.org:9000/health"


def test_parse_http_target_allows_lan_and_loopback() -> None:
    """Política SSRF del panel: los monitores SÍ apuntan a la red interna."""
    targets = ("http://127.0.0.1:8000/", "http://localhost/admin", "http://10.0.0.5/")
    for target in targets:
        assert monitors.parse_target("http", target).url == target


@pytest.mark.parametrize(
    "target",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/",
        "ftp://example.org/",
        "192.168.1.1:80",  # sin esquema
        "http://usuario:clave@example.org/",
        "http://",
        "   ",
    ],
)
def test_parse_http_target_rejects_unsafe_schemes(target: str) -> None:
    """Solo se admiten http/https, con host y sin credenciales embebidas."""
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("http", target)


def test_parse_tcp_target_requires_a_port() -> None:
    """Un monitor tcp necesita puerto explícito (campo u ``host:puerto``)."""
    assert monitors.parse_target("tcp", "localhost", 5432).url == "localhost:5432"
    assert monitors.parse_target("tcp", "192.168.1.5:22").port == 22
    assert monitors.parse_target("tcp", "servidor.lan:8080", 80).port == 80
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "192.168.1.5")


def test_parse_ssl_target_defaults_to_443() -> None:
    """Un monitor ssl sin puerto comprueba el 443."""
    assert monitors.parse_target("ssl", "ejemplo.com").url == "ejemplo.com:443"
    assert monitors.parse_target("ssl", "ejemplo.com:8443").port == 8443


def test_parse_socket_accepts_bracketed_ipv6() -> None:
    """Una IPv6 debe ir entre corchetes para poder llevar puerto."""
    parsed = monitors.parse_target("tcp", "[::1]:5432")
    assert (parsed.host, parsed.port) == ("::1", 5432)
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "::1")
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "[::1")


def test_parse_socket_rejects_invalid_port_and_host() -> None:
    """Un puerto o un host mal formados no llegan ni a la base de datos."""
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "servidor:abc")
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "servidor:70000")
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("tcp", "/var/run/socket")


def test_unknown_kind_is_rejected() -> None:
    """Un tipo de monitor inexistente no se acepta."""
    with pytest.raises(monitors.TargetError):
        monitors.parse_target("icmp", "localhost")


# ============================= Cálculo de uptime =============================
def test_uptime_percentage_counts_up_over_total() -> None:
    """El porcentaje es ``up / total * 100`` de la ventana pedida."""
    monitor = _monitor()
    for index in range(4):
        _beat(monitor["id"], up=index != 2, age_s=index * 60)
    assert monitors.uptime_percentage(monitor["id"], 3600) == 75.0


def test_uptime_is_none_without_data() -> None:
    """Sin heartbeats no se inventa un 100 %: se devuelve ``None``."""
    monitor = _monitor()
    assert monitors.uptime_percentage(monitor["id"], 3600) is None
    assert monitors.uptime_summary(monitor["id"]) == {
        "uptime_24h": None,
        "uptime_7d": None,
        "uptime_30d": None,
    }


def test_uptime_windows_are_independent() -> None:
    """Cada ventana cuenta solo los heartbeats que caen dentro de ella."""
    monitor = _monitor()
    _beat(monitor["id"], up=False, age_s=60)        # caída hace 1 minuto
    _beat(monitor["id"], up=True, age_s=3600)       # bien hace 1 hora
    _beat(monitor["id"], up=True, age_s=3 * 86400)  # bien hace 3 días
    summary = monitors.uptime_summary(monitor["id"])
    assert summary["uptime_24h"] == 50.0     # solo los dos primeros
    assert summary["uptime_7d"] == 66.67     # los tres
    assert summary["uptime_30d"] == 66.67


def test_monitor_summary_reports_status_and_ssl_days() -> None:
    """El resumen añade estado, latencia, último chequeo y días de certificado."""
    monitor = _monitor(kind="ssl", target="ejemplo.com")
    monitors.insert_heartbeat(monitor["id"], int(time.time()), True, 8.0, None, "87")
    summary = monitors.monitor_summary(monitor)
    assert summary["status"] == "up"
    assert summary["latency_ms"] == 8.0
    assert str(summary["last_check"]).endswith("+00:00")
    assert summary["ssl_days_left"] == 87
    assert summary["uptime_24h"] == 100.0

    # Sin heartbeats el estado es «unknown» y no hay días de certificado.
    fresh = monitors.monitor_summary(_monitor(kind="ssl", target="otro.example"))
    assert fresh["status"] == "unknown"
    assert fresh["ssl_days_left"] is None
    assert fresh["uptime_24h"] is None


def test_monitor_status_reports_down_and_error() -> None:
    """Un último heartbeat fallido deja el monitor en «down» con su error."""
    monitor = _monitor()
    monitors.insert_heartbeat(
        monitor["id"], int(time.time()), False, None, 500, None, "timeout"
    )
    summary = monitors.monitor_summary(monitor)
    assert summary["status"] == "down"
    assert summary["last_error"] == "timeout"


def test_heartbeats_are_returned_oldest_first() -> None:
    """El histórico llega con la muestra más reciente al final."""
    monitor = _monitor()
    for index in range(5):
        _beat(monitor["id"], up=True, age_s=(5 - index) * 60)
    rows = monitors.list_heartbeats(monitor["id"])
    timestamps = [int(row["ts"]) for row in rows]
    assert timestamps == sorted(timestamps)


def test_heartbeat_history_is_capped() -> None:
    """El histórico nunca devuelve más de 500 puntos."""
    monitor = _monitor()
    conn = db.connect()
    try:
        now = int(time.time())
        conn.executemany(
            "INSERT INTO heartbeats (id, monitor_id, ts, up, latency_ms) "
            "VALUES (?, ?, ?, 1, 5.0)",
            [(index + 1, monitor["id"], now - index) for index in range(600)],
        )
        conn.commit()
    finally:
        conn.close()
    assert len(monitors.list_heartbeats(monitor["id"])) == monitors.MAX_HEARTBEATS


def test_prune_removes_only_old_heartbeats() -> None:
    """La purga respeta la retención y deja el histórico reciente intacto."""
    monitor = _monitor()
    _beat(monitor["id"], up=True, age_s=10)
    _beat(monitor["id"], up=True, age_s=monitors.HEARTBEAT_RETENTION_S + 3600)
    assert monitors.prune_heartbeats(0) == 1
    assert len(monitors.list_heartbeats(monitor["id"])) == 1


# ========================= Comprobantes con dobles =========================
class _FakeStream:
    """Respuesta simulada de ``httpx`` (``status_code`` + cuerpo por trozos)."""

    def __init__(self, status_code: int, body: bytes = b"") -> None:
        self.status_code = status_code
        self._body = body

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def aiter_bytes(self) -> Any:
        yield self._body


class _FakeClient:
    """Cliente httpx mínimo con el ``stream`` que usa ``probe_http``.

    Se inyecta sustituyendo ``httpx.AsyncClient``, así que además permite
    comprobar que la comprobación abre el cliente con el timeout del monitor y
    **sin** seguir redirecciones.
    """

    def __init__(
        self, status_code: int = 200, body: bytes = b"", error: Exception | None = None
    ) -> None:
        self._status_code = status_code
        self._body = body
        self._error = error
        self.calls: list[str] = []
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> "_FakeClient":
        """Sustituye a ``httpx.AsyncClient(...)``: guarda sus argumentos."""
        self.kwargs = kwargs
        return self

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakeStream:
        self.calls.append(f"{method} {url}")
        if self._error is not None:
            raise self._error
        return _FakeStream(self._status_code, self._body)


def _http_monitor(**overrides: Any) -> dict[str, Any]:
    """Fila de monitor tal y como la recibe un comprobante."""
    base: dict[str, Any] = {
        "id": 1,
        "name": "Web",
        "kind": "http",
        "target": "http://servidor.lan/health",
        "port": None,
        "timeout_s": 5,
        "expected_status": None,
        "keyword": None,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("status_code", "expected_up"),
    [(200, True), (204, True), (301, True), (404, False), (500, False)],
)
def test_http_probe_accepts_2xx_and_3xx(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_up: bool
) -> None:
    """Sin ``expected_status`` está arriba cualquier 2xx/3xx y caído el resto."""
    fake = _FakeClient(status_code)
    monkeypatch.setattr(monitor_checker.httpx, "AsyncClient", fake)
    result = asyncio.run(monitor_checker.probe_http(_http_monitor()))
    assert result.up is expected_up
    assert result.status_code == status_code
    assert result.latency_ms is not None
    assert result.detail == f"HTTP {status_code}"
    assert fake.calls == ["GET http://servidor.lan/health"]
    # Nunca se siguen redirecciones: un 30x no desviaría el destino vigilado.
    assert fake.kwargs["follow_redirects"] is False
    assert fake.kwargs["timeout"] == 5.0


def test_http_probe_honours_expected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con ``expected_status`` solo cuenta el código exacto."""
    monkeypatch.setattr(monitor_checker.httpx, "AsyncClient", _FakeClient(302))
    result = asyncio.run(monitor_checker.probe_http(_http_monitor(expected_status=200)))
    assert result.up is False

    monkeypatch.setattr(monitor_checker.httpx, "AsyncClient", _FakeClient(302))
    result = asyncio.run(monitor_checker.probe_http(_http_monitor(expected_status=302)))
    assert result.up is True


def test_http_probe_checks_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si hay palabra clave, el cuerpo debe contenerla."""
    monkeypatch.setattr(
        monitor_checker.httpx, "AsyncClient", _FakeClient(200, b"<h1>Nexus-Core OK</h1>")
    )
    found = asyncio.run(monitor_checker.probe_http(_http_monitor(keyword="Nexus-Core")))
    assert found.up is True
    assert found.detail == "HTTP 200"

    monkeypatch.setattr(
        monitor_checker.httpx, "AsyncClient", _FakeClient(200, b"<h1>maintenance</h1>")
    )
    missing = asyncio.run(monitor_checker.probe_http(_http_monitor(keyword="Nexus-Core")))
    assert missing.up is False
    assert "palabra clave" in (missing.detail or "")


def test_http_probe_reads_at_most_64kb(monkeypatch: pytest.MonkeyPatch) -> None:
    """La palabra clave se busca en los primeros 64 KB, no en el cuerpo entero."""
    late = b"x" * monitors.MAX_BODY_BYTES + b"ERROR: caido"
    monkeypatch.setattr(monitor_checker.httpx, "AsyncClient", _FakeClient(200, late))
    result = asyncio.run(monitor_checker.probe_http(_http_monitor(keyword="ERROR")))
    assert result.up is False  # la palabra está, pero más allá del límite leído


def test_http_probe_records_error_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo de red es un heartbeat «caído», nunca una excepción."""
    monkeypatch.setattr(
        monitor_checker.httpx,
        "AsyncClient",
        _FakeClient(error=OSError("connection refused")),
    )
    result = asyncio.run(monitor_checker.probe_http(_http_monitor()))
    assert result.up is False
    assert "connection refused" in (result.error or "")
    assert result.status_code is None


def test_http_probe_rejects_non_http_target() -> None:
    """Un destino manipulado a mano con otro esquema no se comprueba."""
    with pytest.raises(monitors.TargetError):
        asyncio.run(monitor_checker.probe_http(_http_monitor(target="file:///etc/passwd")))


def test_tcp_probe_against_a_real_socket() -> None:
    """El comprobante tcp levanta una conexión real en localhost y la cierra."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    try:
        result = asyncio.run(
            monitor_checker.probe_tcp(
                {
                    "id": 1,
                    "name": "SSH local",
                    "kind": "tcp",
                    "target": "127.0.0.1",
                    "port": port,
                    "timeout_s": 5,
                }
            )
        )
        assert result.up is True
        assert result.latency_ms is not None
        assert result.detail == f"127.0.0.1:{port}"
    finally:
        listener.close()

    # Puerto cerrado: el fallo se registra como error, sin excepción.
    closed = asyncio.run(
        monitor_checker.probe_tcp(
            {"kind": "tcp", "target": "127.0.0.1", "port": 1, "timeout_s": 2}
        )
    )
    assert closed.up is False
    assert closed.error


def test_ssl_probe_reports_down_on_handshake_failure() -> None:
    """Un handshake TLS imposible deja el monitor caído con su error."""
    result = asyncio.run(
        monitor_checker.probe_ssl(
            {"kind": "ssl", "target": "127.0.0.1", "port": 1, "timeout_s": 2}
        )
    )
    assert result.up is False
    assert result.detail is None and result.error


# ============================ Bucle del comprobante ============================
def test_checker_tick_records_heartbeat_and_reloads() -> None:
    """El tick comprueba lo vencido y recoge del registro lo que se borra."""
    seen: list[str] = []

    async def fake_probe(monitor: Any) -> monitor_checker.CheckResult:
        seen.append(str(monitor["name"]))
        return monitor_checker.CheckResult(True, 3.5, 200, "HTTP 200")

    monitor = _monitor()
    checker = monitor_checker.MonitorChecker(probes={"http": fake_probe})
    asyncio.run(checker.tick())
    assert seen == ["Web principal"]
    beat = monitors.last_heartbeat(int(monitor["id"]))
    assert beat is not None
    assert int(beat["up"]) == 1 and beat["status_code"] == 200

    # El intervalo aún no ha vencido: no se repite la comprobación.
    asyncio.run(checker.tick())
    assert seen == ["Web principal"]

    # Al borrarlo desaparece del registro (y con él sus heartbeats).
    monitors.delete_monitor(int(monitor["id"]))
    asyncio.run(checker.tick())
    assert seen == ["Web principal"]
    assert monitors.list_heartbeats(int(monitor["id"])) == []


def test_checker_picks_up_new_and_reenabled_monitors() -> None:
    """Alta y reactivación se aplican sin reiniciar el servicio."""
    checked: list[str] = []

    async def fake_probe(monitor: Any) -> monitor_checker.CheckResult:
        checked.append(str(monitor["name"]))
        return monitor_checker.CheckResult(True, 1.0, 200, "HTTP 200")

    checker = monitor_checker.MonitorChecker(probes={"http": fake_probe})
    asyncio.run(checker.tick())
    assert checked == []

    monitor = _monitor(name="Nuevo")
    asyncio.run(checker.tick())
    assert checked == ["Nuevo"]

    monitors.update_monitor(int(monitor["id"]), {"enabled": False})
    asyncio.run(checker.tick())
    assert checked == ["Nuevo"]

    monitors.update_monitor(int(monitor["id"]), {"enabled": True})
    asyncio.run(checker.tick())
    assert checked == ["Nuevo", "Nuevo"]


def test_checker_survives_a_failing_probe() -> None:
    """Un comprobante que revienta solo afecta a su monitor, no al bucle."""
    ok_calls = 0

    async def boom(_monitor: Any) -> monitor_checker.CheckResult:
        raise RuntimeError("fallo del comprobante")

    async def ok(_monitor: Any) -> monitor_checker.CheckResult:
        nonlocal ok_calls
        ok_calls += 1
        return monitor_checker.CheckResult(True, 1.0, 200, "HTTP 200")

    broken = _monitor(name="Roto")
    good = _monitor(name="Bueno")

    checker = monitor_checker.MonitorChecker(probes={"http": boom})
    asyncio.run(checker.tick())
    beat = monitors.last_heartbeat(int(broken["id"]))
    assert beat["up"] == 0
    assert "fallo del comprobante" in beat["error"]

    checker = monitor_checker.MonitorChecker(probes={"http": ok})
    asyncio.run(checker.tick())
    assert ok_calls == 2  # ambos monitores se comprobaron en la misma vuelta
    assert monitors.last_heartbeat(int(good["id"]))["up"] == 1


def test_checker_marks_invalid_row_down() -> None:
    """Una fila manipulada con un destino inválido se marca caída, sin romper el tick."""
    monitor = _monitor(name="Manipulado", kind="tcp", target="10.0.0.1:22")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE monitors SET target = '10.0.0.1:0' WHERE id = ?", (monitor["id"],)
        )
        conn.commit()
    finally:
        conn.close()

    async def unused(_monitor: Any) -> monitor_checker.CheckResult:
        raise AssertionError("no debería llegar a comprobarse")

    checker = monitor_checker.MonitorChecker(probes={"tcp": unused})
    asyncio.run(checker.tick())
    beat = monitors.last_heartbeat(int(monitor["id"]))
    assert beat["up"] == 0
    assert "puerto" in beat["error"]


def test_checker_stops_without_leaking_tasks() -> None:
    """``stop()`` termina el bucle y la tarea queda finalized."""

    async def scenario() -> None:
        async def fake_probe(_monitor: Any) -> monitor_checker.CheckResult:
            return monitor_checker.CheckResult(True, 1.0, 200, "HTTP 200")

        _monitor()
        checker = monitor_checker.MonitorChecker(probes={"http": fake_probe})
        task = asyncio.create_task(checker.run())
        await asyncio.sleep(0.1)
        await checker.stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


def test_checker_can_be_disabled_by_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con ``MONITORS_ENABLED=false`` el bucle ni siquiera llega a comprobar."""

    async def fake_probe(_monitor: Any) -> monitor_checker.CheckResult:
        raise AssertionError("el comprobador está desactivado")

    checker = monitor_checker.MonitorChecker(probes={"http": fake_probe})
    monkeypatch.setenv("MONITORS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        asyncio.run(asyncio.wait_for(checker.run(), timeout=2))
    finally:
        monkeypatch.delenv("MONITORS_ENABLED", raising=False)
        get_settings.cache_clear()


# ============================== API de monitores ==============================
def test_monitors_api_crud(client: TestClient, csrf: str) -> None:
    """Alta, listado, edición y borrado desde la API privada."""
    created = client.post(
        "/api/monitors",
        json={"name": "Router", "kind": "tcp", "target": "192.168.1.1", "port": 22},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    monitor = created.json()
    assert monitor["status"] == "unknown"
    assert monitor["uptime_24h"] is None
    assert monitor["ssl_days_left"] is None

    listed = client.get("/api/monitors")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    _beat(monitor["id"], up=True)
    patched = client.patch(
        f"/api/monitors/{monitor['id']}",
        json={"interval_s": 120, "enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["interval_s"] == 120
    assert patched.json()["enabled"] == 0
    assert patched.json()["status"] == "up"  # ya tiene heartbeat
    assert patched.json()["uptime_24h"] == 100.0
    # El destino no enviado se conserva.
    assert patched.json()["port"] == 22

    removed = client.delete(
        f"/api/monitors/{monitor['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert removed.status_code == 200 and removed.json()["success"] is True
    assert (
        client.patch(
            f"/api/monitors/{monitor['id']}", json={}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/monitors/{monitor['id']}", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 404
    )


def test_monitors_api_validates_definition(client: TestClient, csrf: str) -> None:
    """Tipo, destino, puerto, intervalo y timeout inválidos se rechazan con 422."""
    invalid = [
        {"name": "a", "kind": "gopher", "target": "gopher://x/"},
        {"name": "a", "kind": "http", "target": "file:///etc/passwd"},
        {"name": "a", "kind": "http", "target": "192.168.1.1"},
        {"name": "a", "kind": "tcp", "target": "192.168.1.1"},  # sin puerto
        {"name": "a", "kind": "tcp", "target": "host", "port": 70000},
        {"name": "a", "kind": "http", "target": "http://x", "interval_s": 5},
        {"name": "a", "kind": "http", "target": "http://x", "timeout_s": 90},
        {"name": "", "kind": "http", "target": "http://x"},
    ]
    for body in invalid:
        response = client.post(
            "/api/monitors", json=body, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 422, (body, response.text)
    assert client.get("/api/monitors").json()["count"] == 0

    # ssl sí acepta destino sin puerto (443 por defecto).
    assert (
        client.post(
            "/api/monitors",
            json={"name": "Web segura", "kind": "ssl", "target": "ejemplo.com"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 201
    )


def test_patch_revalidates_target_against_kind(client: TestClient, csrf: str) -> None:
    """Un PATCH que deja kind y target incoherentes se rechaza con 400."""
    monitor = _monitor(name="Web")
    response = client.patch(
        f"/api/monitors/{monitor['id']}",
        json={"kind": "tcp", "port": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400, response.text


def test_patch_clears_nullable_fields(client: TestClient, csrf: str) -> None:
    """Un ``null`` explícito limpia puerto, código esperado y palabra clave."""
    monitor = _monitor(
        name="Web", target="http://192.168.1.1:8080", port=8080, keyword="OK"
    )
    response = client.patch(
        f"/api/monitors/{monitor['id']}",
        json={"keyword": None, "expected_status": None, "port": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keyword"] is None
    assert body["expected_status"] is None
    assert body["port"] is None
    # El destino no se toca al limpiar el puerto: se usa el de la URL.
    assert body["target"] == "http://192.168.1.1:8080"


def test_heartbeats_endpoint(client: TestClient) -> None:
    """El histórico devuelve los heartbeats con el más reciente al final."""
    monitor = _monitor(name="Histórico")
    for index in range(3):
        _beat(monitor["id"], up=index != 1, age_s=(3 - index) * 60)

    response = client.get(f"/api/monitors/{monitor['id']}/heartbeats?hours=24")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["monitor_id"] == monitor["id"]
    assert body["hours"] == 24
    assert body["count"] == 3
    assert [point["up"] for point in body["points"]] == [1, 0, 1]
    assert body["points"][-1]["ts"] >= body["points"][0]["ts"]
    assert body["points"][0]["iso"].endswith("+00:00")

    assert client.get(f"/api/monitors/{monitor['id']}/heartbeats?hours=0").status_code == 422
    assert client.get("/api/monitors/9999/heartbeats").status_code == 404


def test_monitors_endpoints_require_authentication() -> None:
    """Sin sesión, los endpoints de monitores responden 401."""
    anonymous = TestClient(app)
    for path in ("/api/monitors", "/api/monitors/1/heartbeats"):
        assert anonymous.get(path).status_code == 401, path
    assert (
        anonymous.post(
            "/api/monitors",
            json={"name": "x", "kind": "tcp", "target": "192.168.1.1:22"},
        ).status_code
        == 401
    )
    assert (
        anonymous.patch(
            "/api/monitors/1", json={"name": "x"}, headers={"X-CSRF-Token": "falso"}
        ).status_code
        == 401
    )
    assert anonymous.delete("/api/monitors/1").status_code == 401


def test_csrf_is_required_for_mutations(client: TestClient) -> None:
    """Con sesión válida pero sin X-CSRF-Token el alta se rechaza (403)."""
    response = client.post(
        "/api/monitors",
        json={"name": "Sin CSRF", "kind": "tcp", "target": "192.168.1.1:22"},
    )
    assert response.status_code == 403


# ============================== Estado público ==============================
def test_public_status_requires_no_auth_and_hides_targets() -> None:
    """El estado público es accesible sin sesión y no filtra el destino."""
    secret = _monitor(name="Router del laboratorio", target="http://192.168.1.1:8080/admin")
    monitors.insert_heartbeat(
        secret["id"], int(time.time()), False, None, 500, "HTTP 500", "connection refused"
    )
    private = _monitor(name="Web interna", kind="tcp", target="10.0.0.9:22", port=22)
    monitors.insert_heartbeat(
        int(private["id"]), int(time.time()), True, 4.0, None, "10.0.0.9:22"
    )

    response = TestClient(app).get("/api/status/public")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall"] == "down"  # hay un monitor caído
    assert body["updated_at"].endswith("+00:00")
    assert [entry["name"] for entry in body["monitors"]] == [
        "Router del laboratorio",
        "Web interna",
    ]
    assert body["monitors"][0]["status"] == "down"
    assert body["monitors"][1]["status"] == "up"
    # Solo los campos del contrato público: ningún destino ni error puede salir.
    assert set(body["monitors"][0]) == {
        "name",
        "kind",
        "status",
        "latency_ms",
        "last_check",
        "uptime_24h",
        "uptime_7d",
    }
    assert "192.168.1.1" not in response.text
    assert "8080" not in response.text
    assert "10.0.0.9" not in response.text
    assert "connection refused" not in response.text


def test_public_status_overall_states() -> None:
    """``up`` con todo sano, ``down`` con algún caído, ``degraded`` sin datos."""
    assert monitors.overall_status([]) == "up"
    assert monitors.overall_status(["up", "up"]) == "up"
    assert monitors.overall_status(["up", "unknown"]) == "degraded"
    assert monitors.overall_status(["unknown", "down"]) == "down"

    # Vía endpoint: uno sano y otro aún sin comprobar -> degradado.
    healthy = _monitor(name="Sano")
    monitors.insert_heartbeat(healthy["id"], int(time.time()), True, 1.0, 200, "HTTP 200")
    _monitor(name="Sin comprobar")
    response = TestClient(app).get("/api/status/public")
    assert response.status_code == 200
    assert response.json()["overall"] == "degraded"


def test_public_status_is_cached_briefly() -> None:
    """La respuesta pública lleva una caché corta."""
    response = TestClient(app).get("/api/status/public")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=15"


def test_status_page_is_selfcontained() -> None:
    """La página de estado es HTML embebido, sin recursos externos ni datos."""
    response = TestClient(app).get("/api/status/page")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "/api/status/public" in body
    assert "http://" not in body  # ningún recurso externo
    assert "<script src=" not in body
    assert "stylesheet" not in body
    assert response.headers["cache-control"] == "public, max-age=15"


# ================================ Prometheus ================================
def test_prometheus_escape_label_and_values() -> None:
    """Se escapan barra invertida, comillas y saltos de línea de las etiquetas."""
    assert prometheus.escape_label('web "principal"') == 'web \\"principal\\"'
    assert prometheus.escape_label("a\\b") == "a\\\\b"
    assert prometheus.escape_label("a\nb") == "a\\nb"
    assert prometheus.format_value(0) == "0"
    assert prometheus.format_value(12.5) == "12.5"
    assert prometheus.format_value(1234567.0) == "1234567"  # sin notación exponencial


def test_prometheus_exports_system_metrics_from_latest_sample() -> None:
    """Las métricas del host salen de la última muestra guardada."""
    metrics_store.insert_sample(
        int(time.time()),
        {"cpu": 12.5, "ram": 40.0, "swap": 1.0, "disk": 55.0, "load1": 0.4, "temp": None},
    )
    body = prometheus.render()
    assert "# HELP nexus_cpu_percent Uso de CPU del host en porcentaje." in body
    assert "# TYPE nexus_cpu_percent gauge" in body
    assert "nexus_cpu_percent 12.5" in body
    assert "nexus_ram_percent 40" in body
    assert "nexus_load1 0.4" in body
    assert "nexus_uptime_seconds " in body
    # Sin sensor de temperatura la familia no se publica (nada de ceros falsos).
    assert "nexus_temperature_celsius" not in body


def test_prometheus_exports_temperature_when_available() -> None:
    """Con sensor, la temperatura se publica como Celsius."""
    metrics_store.insert_sample(
        int(time.time()),
        {"cpu": 1.0, "ram": 2.0, "swap": 0.0, "disk": 3.0, "load1": 0.1, "temp": 57.5},
    )
    body = prometheus.render()
    assert "nexus_temperature_celsius 57.5" in body


def test_prometheus_exports_monitors_with_escaped_labels() -> None:
    """Cada monitor habilitado publica su estado y su latencia."""
    monitor = _monitor(name='Web "principal" \\n')
    monitors.insert_heartbeat(monitor["id"], int(time.time()), True, 21.25, 200, "HTTP 200")
    down = _monitor(name="Router")
    monitors.insert_heartbeat(
        down["id"], int(time.time()), False, None, 500, "HTTP 500", "timeout"
    )

    body = prometheus.render()
    assert 'nexus_monitor_up{monitor="Web \\"principal\\" \\\\n"} 1' in body
    assert 'nexus_monitor_latency_ms{monitor="Web \\"principal\\" \\\\n"} 21.25' in body
    assert 'nexus_monitor_up{monitor="Router"} 0' in body
    # El nombre escapado no debe romper la línea de la muestra.
    assert 'nexus_monitor_up{monitor="Web "principal"' not in body


def test_prometheus_skips_monitors_without_heartbeats() -> None:
    """Sin comprobaciones no se inventa un valor para el monitor."""
    _monitor(name="Sin datos")
    assert "nexus_monitor_up" not in prometheus.render()


def test_prometheus_skips_disabled_monitors() -> None:
    """Un monitor desactivado no publica un estado congelado."""
    monitor = _monitor(name="Pausado", enabled=False)
    monitors.insert_heartbeat(
        monitor["id"], int(time.time()), False, None, 500, "HTTP 500"
    )
    assert "nexus_monitor_up" not in prometheus.render()


def test_prometheus_counts_open_alerts_by_severity() -> None:
    """``nexus_alerts_open`` publica crit y warn con el recuento de no cerrados."""
    alerts.reset_state()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM alert_events")
        conn.commit()
    finally:
        conn.close()
    _insert_alert(1, "crit")
    _insert_alert(2, "crit")
    _insert_alert(3, "warn")
    _insert_alert(4, "crit", status="closed")  # cerrado: no cuenta

    body = prometheus.render()
    assert "# TYPE nexus_alerts_open gauge" in body
    assert 'nexus_alerts_open{severity="crit"} 2' in body
    assert 'nexus_alerts_open{severity="warn"} 1' in body


def test_prometheus_endpoint_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """El endpoint sirve el Content-Type del formato de texto 0.0.4."""
    monkeypatch.setenv("METRICS_TOKEN", "metrics-de-prueba")
    get_settings.cache_clear()
    try:
        response = TestClient(app).get(
            "/api/metrics/prometheus",
            headers={"Authorization": "Bearer metrics-de-prueba"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
        assert response.text.endswith("\n")
    finally:
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        get_settings.cache_clear()


def test_prometheus_is_closed_without_token() -> None:
    """Sin METRICS_TOKEN el exportador no se publica en Internet."""
    assert get_settings().METRICS_TOKEN == ""
    assert TestClient(app).get("/api/metrics/prometheus").status_code == 404


def test_prometheus_requires_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con METRICS_TOKEN se exige «Authorization: Bearer <token>» (401 si no)."""
    monkeypatch.setenv("METRICS_TOKEN", "secreto-de-prueba")
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        assert client.get("/api/metrics/prometheus").status_code == 401
        assert (
            client.get(
                "/api/metrics/prometheus", headers={"Authorization": "Bearer otro"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/metrics/prometheus", headers={"Authorization": "secreto-de-prueba"}
            ).status_code
            == 401  # falta el esquema Bearer
        )
        ok = client.get(
            "/api/metrics/prometheus",
            headers={"Authorization": "Bearer secreto-de-prueba"},
        )
        assert ok.status_code == 200
        assert "nexus_uptime_seconds" in ok.text
    finally:
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        get_settings.cache_clear()
