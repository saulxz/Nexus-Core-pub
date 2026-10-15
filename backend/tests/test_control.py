"""Tests del control de servicios (Fase 2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import privileged
from app.modules import service_control


def test_control_rejects_bad_action() -> None:
    """Una acción fuera de la lista se rechaza sin tocar nada."""
    result = service_control.control("smbd.service", "destroy", "tester")
    assert result["success"] is False
    assert "no permitida" in result["message"]


def test_control_rejects_bad_unit() -> None:
    """Un nombre de unidad con inyección se rechaza."""
    result = service_control.control("smbd; rm -rf /", "restart", "tester")
    assert result["success"] is False


def test_control_graceful_without_privileges() -> None:
    """Sin sudo/systemd el control degrada con un mensaje claro."""
    if privileged.available():
        pytest.skip("Aplica solo en sistemas sin sudo/systemd")
    result = service_control.control("smbd.service", "restart", "tester")
    assert result["success"] is False
    assert "Linux" in result["message"]


def test_action_endpoint_requires_csrf(client: TestClient) -> None:
    """Sin cabecera CSRF la acción se rechaza (403)."""
    response = client.post("/api/services/smbd.service/action", json={"action": "restart"})
    assert response.status_code == 403


def test_action_endpoint_rejects_invalid_action(client: TestClient, csrf: str) -> None:
    """Una acción no permitida por el esquema devuelve 422."""
    response = client.post(
        "/api/services/smbd.service/action",
        json={"action": "delete"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_action_endpoint_graceful(client: TestClient, csrf: str) -> None:
    """En un sistema sin helpers responde 200 con success=False."""
    response = client.post(
        "/api/services/smbd.service/action",
        json={"action": "restart"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    body = response.json()
    if privileged.available():
        pytest.skip("En Linux con helpers el resultado depende del sistema real")
    assert body["success"] is False
