"""Configuración común de los tests de Nexus-Core.

* Aísla el estado de la aplicación en un directorio temporal
  (``NEXUS_DATA_DIR``) para no tocar datos reales.
* Proporciona un cliente autenticado: completa el primer arranque con el
  token de setup y deja la cookie de sesión en el cliente de tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# El entorno debe configurarse ANTES de importar la aplicación.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="nexus-tests-"))
os.environ["NEXUS_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["SESSION_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TEST_USERNAME = "admin"
TEST_PASSWORD = "contrasena-de-prueba-123"


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Cliente autenticado (completa el setup inicial y guarda la cookie)."""
    with TestClient(app) as test_client:
        status = test_client.get("/api/auth/setup-status").json()
        if status.get("needs_setup"):
            from app.core.security import read_setup_token

            token = read_setup_token() or ""
            response = test_client.post(
                "/api/auth/setup",
                json={
                    "token": token,
                    "username": TEST_USERNAME,
                    "password": TEST_PASSWORD,
                },
            )
            assert response.status_code == 200, response.text
        else:
            response = test_client.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            )
            assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture(scope="session")
def csrf(client: TestClient) -> str:
    """Token CSRF de la sesión de tests."""
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf"]
