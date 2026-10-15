"""Tests del inventario de servicios (Fase 1, solo lectura).

Ejecutar con:
    python -m pytest -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules import systemd_manager


def test_normalize_unit_accepts_valid_names() -> None:
    """Nombres legítimos se normalizan a '<nombre>.service'."""
    assert systemd_manager.normalize_unit("smbd.service") == "smbd.service"
    assert systemd_manager.normalize_unit("nginx") == "nginx.service"
    assert systemd_manager.normalize_unit(" getty@tty1.service ") == "getty@tty1.service"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "smbd.service; rm -rf /", "../../etc/passwd", "a b.service", "x" * 200],
)
def test_normalize_unit_rejects_injection(bad: str) -> None:
    """Nombres con caracteres raros o inyecciones se rechazan con ValueError."""
    with pytest.raises(ValueError):
        systemd_manager.normalize_unit(bad)


def test_services_endpoint_shape(client: TestClient) -> None:
    """El inventario responde con la forma esperada (disponible o no)."""
    response = client.get("/api/services")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["available"], bool)
    assert body["count"] == len(body["services"])


def test_services_requires_auth() -> None:
    """Sin sesión no se puede leer el inventario."""
    anonymous = TestClient(app)
    assert anonymous.get("/api/services").status_code == 401


def test_service_detail_rejects_bad_name(client: TestClient) -> None:
    """Un nombre de unidad inválido responde 400 y no ejecuta nada."""
    response = client.get("/api/services/no%20valido")
    assert response.status_code == 400
