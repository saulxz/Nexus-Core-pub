"""Tests básicos de la API de Nexus-Core.

Ejecutar con:
    pytest -q
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    """El endpoint de salud debe responder 200 con status 'ok'."""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Nexus-Core"


def test_metrics() -> None:
    """El endpoint de métricas debe devolver las claves esperadas."""
    response = client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    for key in ("cpu_percent", "ram_percent", "disk_percent", "timestamp"):
        assert key in body


def test_network_scan_rejects_public_subnet() -> None:
    """No debe permitirse escanear rangos públicos (medida de seguridad)."""
    response = client.post("/api/network-scan", json={"subnet": "8.8.8.0/24"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["error"]


def test_containers_endpoint() -> None:
    """El endpoint de contenedores debe responder 200 con la clave 'containers'."""
    response = client.get("/api/containers")
    assert response.status_code == 200
    assert "containers" in response.json()
