"""Tests de configuración de red con watchdog (Fase 6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.modules import network_config


class _Plan:
    """Doble simple con la misma forma que NetworkPlanRequest."""

    def __init__(self, **overrides) -> None:
        self.interface = "eth0"
        self.address = "192.168.1.50"
        self.prefix = 24
        self.gateway = "192.168.1.1"
        self.dns = ["192.168.1.1"]
        for key, value in overrides.items():
            setattr(self, key, value)


def test_plan_rejects_public_address() -> None:
    """No se permiten direcciones públicas para la VM."""
    result = network_config.plan(_Plan(address="8.8.8.8"))
    assert result["ok"] is False


def test_plan_rejects_bad_prefix() -> None:
    """El prefijo fuera de rango se rechaza."""
    assert network_config.plan(_Plan(prefix=31))["ok"] is False
    assert network_config.plan(_Plan(prefix=4))["ok"] is False


def test_plan_preview_contains_netplan() -> None:
    """La previsualización incluye el fichero netplan con la IP nueva."""
    result = network_config.plan(_Plan())
    assert "network:" in result["preview"]
    assert "192.168.1.50/24" in result["preview"]
    assert "Gestionado por Nexus-Core" in result["preview"]


def test_apply_graceful_without_privileges() -> None:
    """Sin helpers, aplicar devuelve fallo claro y sin token."""
    if network_config.privileged_available() and network_config.detect_renderer()["supported"]:
        return  # En Linux con netplan dependería del sistema real
    result = network_config.apply_change(_Plan(), "tester")
    assert result["success"] is False
    assert result["token"] is None


def test_token_validation() -> None:
    """Tokens mal formados se rechazan en confirm/revert."""
    assert network_config.confirm_change("nope", "tester")["success"] is False
    assert network_config.revert_change("../../x", "tester")["success"] is False


def test_network_api(client: TestClient, csrf: str) -> None:
    """Flujo de la API de red (plan, validación y estado)."""
    response = client.get("/api/network/state")
    assert response.status_code == 200
    assert "renderer" in response.json()

    response = client.post(
        "/api/network/plan",
        json={
            "interface": "eth0",
            "address": "192.168.1.50",
            "prefix": 24,
            "gateway": "192.168.1.1",
            "dns": ["1.1.1.1"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert "preview" in response.json()

    response = client.post(
        "/api/network/plan",
        json={
            "interface": "eth0",
            "address": "8.8.8.8",
            "prefix": 24,
            "gateway": "192.168.1.1",
            "dns": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
