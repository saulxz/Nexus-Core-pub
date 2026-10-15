"""Tests de la API de Nexus-Core (Fase 0: autenticación y seguridad).

Ejecutar con:
    python -m pytest -q
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app.core.audit import verify_chain
from app.main import app


def test_health_is_public() -> None:
    """El healthcheck no requiere autenticación (lo consulta Docker)."""
    anonymous = TestClient(app)
    response = anonymous.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Nexus-Core"


def test_api_requires_authentication(client: TestClient) -> None:
    """Sin sesión, los endpoints protegidos responden 401."""
    anonymous = TestClient(app)
    for path in ("/api/metrics", "/api/containers", "/api/settings/ai"):
        response = anonymous.get(path)
        assert response.status_code == 401, path


def test_metrics_with_session(client: TestClient) -> None:
    """Con sesión válida, la telemetría responde con las claves esperadas."""
    response = client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    for key in ("cpu_percent", "ram_percent", "disk_percent", "timestamp"):
        assert key in body


def test_network_scan_rejects_public_subnet(client: TestClient, csrf: str) -> None:
    """No debe permitirse escanear rangos públicos (medida de seguridad)."""
    response = client.post(
        "/api/network-scan",
        json={"subnet": "8.8.8.0/24"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["error"]


def test_mutations_require_csrf(client: TestClient) -> None:
    """Sin cabecera CSRF, un método mutante responde 403."""
    response = client.post("/api/network-scan", json={"subnet": "192.168.1.0/24"})
    assert response.status_code == 403


@pytest.mark.skipif(
    sys.platform != "linux", reason="Requiere socket de Docker (solo Linux)"
)
def test_containers_endpoint(client: TestClient) -> None:
    """El endpoint de contenedores responde con la clave 'containers'."""
    response = client.get("/api/containers")
    assert response.status_code == 200
    assert "containers" in response.json()


def test_ai_settings_roundtrip(client: TestClient, csrf: str) -> None:
    """La configuración de IA se lee, se guarda desde la web y persiste."""
    response = client.get("/api/settings/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] in ("", "openai", "anthropic", "gemini", "ollama")
    assert set(body["keys"].keys()) == {"openai", "anthropic", "gemini"}

    updated = client.put(
        "/api/settings/ai",
        json={"provider": "gemini", "model": "gemini-2.5-flash"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["provider"] == "gemini"

    reread = client.get("/api/settings/ai")
    assert reread.json()["provider"] == "gemini"
    assert reread.json()["model"] == "gemini-2.5-flash"

    # Limpieza: volver a OpenAI para no condicionar el resto de la sesión.
    client.put(
        "/api/settings/ai",
        json={"provider": "openai", "model": "gpt-4o-mini"},
        headers={"X-CSRF-Token": csrf},
    )


def test_ai_settings_rejects_invalid_provider(client: TestClient, csrf: str) -> None:
    """Un proveedor no soportado se rechaza con 400."""
    response = client.put(
        "/api/settings/ai",
        json={"provider": "skynet"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_ai_settings_provider_is_optional(client: TestClient, csrf: str) -> None:
    """El proveedor puede quedar vacío: el asistente queda desactivado."""
    response = client.put(
        "/api/settings/ai",
        json={"provider": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["provider"] == ""
    # Restauración para no condicionar el resto de la sesión de tests.
    client.put(
        "/api/settings/ai",
        json={"provider": "openai", "model": "gpt-4o-mini"},
        headers={"X-CSRF-Token": csrf},
    )


def test_audit_chain_is_valid(client: TestClient) -> None:
    """La cadena de auditoría permanece íntegra tras la actividad de los tests."""
    result = verify_chain()
    assert result["valid"] is True, result
    assert result["events"] > 0


def test_login_rejects_wrong_password(client: TestClient) -> None:
    """Credenciales incorrectas devuelven 401."""
    anonymous = TestClient(app)
    response = anonymous.post(
        "/api/auth/login", json={"username": "admin", "password": "incorrecta"}
    )
    assert response.status_code == 401
