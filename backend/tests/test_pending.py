"""Tests de las propuestas de IA con confirmación humana (Fase 5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai import pending
from app.ai.tool_executor import execute_tool


@pytest.fixture(autouse=True)
def _ensure_db() -> None:
    """Garantiza la tabla de propuestas (idempotente)."""
    pending.init_db()


def test_pending_lifecycle() -> None:
    """Crear -> confirmar (solo el actor dueño, una única vez)."""
    pending.set_actor("tester")
    action = pending.create(
        "proponer_accion_servicio",
        {"unidad": "smbd.service", "accion": "restart"},
        "restart de smbd.service",
    )
    assert pending.get(action["id"])["status"] == "pending"

    denied = pending.confirm(action["id"], "otro")
    assert denied["success"] is False and denied["reason"] == "forbidden"

    ok = pending.confirm(action["id"], "tester")
    assert ok["success"] is True

    again = pending.confirm(action["id"], "tester")
    assert again["success"] is False and again["reason"] == "state"


def test_pending_expires() -> None:
    """Una propuesta caducada no se puede confirmar."""
    pending.set_actor("tester")
    action = pending.create(
        "proponer_accion_servicio",
        {"unidad": "x.service", "accion": "start"},
        "x",
        ttl=-5,
    )
    outcome = pending.confirm(action["id"], "tester")
    assert outcome["success"] is False and outcome["reason"] == "expired"


def test_executor_proposes_without_executing() -> None:
    """proponer_* crea la propuesta y NO ejecuta; un 'confirm' inyectado se ignora."""
    pending.set_actor("tester-exec")
    result = execute_tool(
        "proponer_accion_servicio",
        {"unidad": "smbd", "accion": "restart", "confirm": True},
    )
    assert result["success"] is True
    assert result["pending"] is True
    stored = pending.get(result["action_id"])
    assert stored["status"] == "pending"
    assert stored["args"]["unidad"] == "smbd.service"


def test_executor_rejects_unknown_action() -> None:
    """Acciones fuera del catálogo se rechazan."""
    result = execute_tool(
        "proponer_accion_servicio", {"unidad": "smbd", "accion": "wipe"}
    )
    assert result["success"] is False


def test_executor_lists_services() -> None:
    """listar_servicios responde siempre (aunque systemd no exista)."""
    result = execute_tool("listar_servicios", {})
    assert result["success"] is True
    assert "services" in result


def test_executor_reads_config() -> None:
    """leer_config_servicio devuelve el modelo del servicio pedido."""
    result = execute_tool("leer_config_servicio", {"servicio": "samba"})
    assert result["success"] is True
    assert "config" in result


def test_chat_actions_api(client: TestClient, csrf: str) -> None:
    """API de propuestas: listar, 404 en desconocida y cancelar."""
    response = client.get("/api/chat/actions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    response = client.post(
        "/api/chat/actions/noexiste00000000/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404

    pending.set_actor("admin")
    action = pending.create(
        "proponer_accion_servicio",
        {"unidad": "smbd.service", "accion": "restart"},
        "prueba",
    )
    response = client.post(
        f"/api/chat/actions/{action['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
