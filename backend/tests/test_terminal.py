"""Barreras de seguridad del WebSocket de la terminal SSH."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def _expect_close(client: TestClient, code: int, **kwargs: object) -> None:
    """Abre el WebSocket y comprueba el código de cierre del servidor."""
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/terminal/ws", **kwargs) as websocket:
            websocket.receive_text()
    assert caught.value.code == code


def test_terminal_targets_requires_authentication() -> None:
    """El listado de destinos no es público."""
    response = TestClient(app).get("/api/terminal/targets")
    assert response.status_code == 401


def test_terminal_targets_lists_local_host(client: TestClient) -> None:
    """El inventario incluye el host local y su disponibilidad."""
    response = client.get("/api/terminal/targets")
    assert response.status_code == 200
    targets = response.json()["targets"]
    assert any(target["is_local"] for target in targets)
    for target in targets:
        assert set(target) == {"id", "name", "is_local", "available", "reason", "transport"}


def test_terminal_rejects_anonymous_session() -> None:
    """Sin cookie de sesión no se acepta el WebSocket (4401)."""
    _expect_close(TestClient(app), 4401)


def test_terminal_rejects_missing_csrf(client: TestClient) -> None:
    """Con sesión válida pero sin subprotocolo CSRF se rechaza (4403)."""
    _expect_close(client, 4403)


def test_terminal_rejects_wrong_csrf(client: TestClient) -> None:
    """Un CSRF que no coincide con la sesión también se rechaza (4403)."""
    _expect_close(client, 4403, subprotocols=["nexus-terminal", "csrf.no-es-el-token"])


def test_terminal_rejects_cross_origin(client: TestClient, csrf: str) -> None:
    """Con sesión y CSRF válidos, un origen ajeno se rechaza (4403)."""
    _expect_close(
        client,
        4403,
        subprotocols=["nexus-terminal", f"csrf.{csrf}"],
        headers={"origin": "https://evil.example"},
    )

