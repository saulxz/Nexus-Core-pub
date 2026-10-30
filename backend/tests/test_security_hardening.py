"""Pruebas de endurecimiento web, secretos y validaciones de seguridad."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import secret_storage, security, totp
from app.core.config import get_settings
from app.main import app
from app.schemas.models import SambaShare


def test_security_headers_are_present() -> None:
    """El panel público añade CSP, HSTS y cabeceras de aislamiento."""
    anonymous = TestClient(app)
    response = anonymous.get("/api/health", headers={"host": "nexus.labsxz.com"})
    assert response.status_code == 200
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_http_redirects_when_cloudflare_says_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si CF-Visitor indica HTTP, el panel público responde 308 a HTTPS."""
    monkeypatch.setenv("ENFORCE_HTTPS", "true")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        response = client.get(
            "/login.html",
            headers={"host": "nexus.labsxz.com", "cf-visitor": '{"scheme":"http"}'},
            follow_redirects=False,
        )
        assert response.status_code == 308
        assert response.headers["location"].startswith("https://nexus.labsxz.com/")
    finally:
        monkeypatch.delenv("ENFORCE_HTTPS", raising=False)
        get_settings.cache_clear()


def test_read_token_cannot_read_enrollment(client: TestClient, csrf: str) -> None:
    """Un token read no puede obtener el secreto de enrolamiento."""
    created_read = client.post(
        "/api/tokens",
        json={"name": "test-read", "scope": "read", "ttl_days": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert created_read.status_code == 201, created_read.text
    read_token = created_read.json()["token"]
    read_id = created_read.json()["id"]

    denied = client.get(
        "/api/hosts/enrollment",
        headers={"Authorization": f"Bearer {read_token}", "X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403

    created_write = client.post(
        "/api/tokens",
        json={"name": "test-write", "scope": "write", "ttl_days": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert created_write.status_code == 201, created_write.text
    write_token = created_write.json()["token"]
    write_id = created_write.json()["id"]
    allowed = client.get(
        "/api/hosts/enrollment",
        headers={"Authorization": f"Bearer {write_token}", "X-CSRF-Token": csrf},
    )
    assert allowed.status_code == 200

    client.delete(f"/api/tokens/{read_id}", headers={"X-CSRF-Token": csrf})
    client.delete(f"/api/tokens/{write_id}", headers={"X-CSRF-Token": csrf})


def test_samba_comment_rejects_line_breaks() -> None:
    """El comentario no puede inyectar directivas en smb.conf."""
    with pytest.raises(ValidationError):
        SambaShare(
            name="datos",
            path="/srv/datos",
            comment="ok\n root preexec = /bin/true",
        )


def test_agent_rejects_non_https_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """El agente solo acepta HTTPS (o HTTP loopback con flag de desarrollo)."""
    from agent import validate_hub_url

    assert validate_hub_url("https://nexus.example.com/") == "https://nexus.example.com"
    with pytest.raises(ValueError):
        validate_hub_url("http://nexus.example.com")
    monkeypatch.setenv("NEXUS_AGENT_ALLOW_HTTP", "1")
    assert validate_hub_url("http://127.0.0.1:8780") == "http://127.0.0.1:8780"


def test_totp_secret_is_encrypted_at_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El secreto TOTP no queda en claro en SQLite y sigue verificando."""
    db_file = tmp_path / "auth.db"
    monkeypatch.setattr(security, "db_path", lambda: db_file)
    monkeypatch.setenv("NEXUS_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    secret_storage._key_cache = None

    security.init_db()
    security.create_admin("admin", "contrasena-de-prueba-123")
    secret = totp.generate_secret()
    security.stage_totp("admin", secret, [])

    conn = sqlite3.connect(str(db_file))
    raw = conn.execute("SELECT totp_secret FROM admin WHERE username = 'admin'").fetchone()[0]
    conn.close()
    assert raw.startswith("enc:v1:")
    assert secret not in raw
    assert security.verify_second_factor("admin", totp.totp_code(secret))["ok"] is True
    secret_storage._key_cache = None


def test_docs_are_disabled() -> None:
    """La documentación interactiva no se publica por defecto."""
    anonymous = TestClient(app)
    assert anonymous.get("/docs").status_code == 404
    assert anonymous.get("/openapi.json").status_code == 404


def test_body_size_limit_is_enforced() -> None:
    """Un cuerpo demasiado grande se rechaza antes de autenticarse."""
    anonymous = TestClient(app)
    oversized = b"x" * (get_settings().MAX_REQUEST_BYTES + 1)
    response = anonymous.post(
        "/api/auth/login",
        content=oversized,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_ai_api_key_is_encrypted_at_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Las claves de IA no quedan en claro en ai.env."""
    from app.core import settings_store

    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEXUS_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    secret_storage._key_cache = None
    settings_store._write_file_map({"GEMINI_API_KEY": "AIza-clave-de-prueba"})

    raw = (tmp_path / "data" / "config" / "ai.env").read_text(encoding="utf-8")
    assert "enc:v1:" in raw
    assert "AIza-clave-de-prueba" not in raw
    assert settings_store._read_file_map()["GEMINI_API_KEY"] == "AIza-clave-de-prueba"
    secret_storage._key_cache = None


def test_backup_is_encrypted(tmp_path: Path) -> None:
    """El backup no deja el tar.gz en claro y se puede descifrar."""
    from cryptography.fernet import Fernet

    script = Path(__file__).resolve().parents[2] / "deploy" / "production" / "backup-nexus.py"
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "backups"
    data_dir.mkdir()
    (data_dir / "config").mkdir()
    (data_dir / "config" / "agent_enroll_token").write_text("secreto-de-prueba", encoding="utf-8")
    db = sqlite3.connect(str(data_dir / "nexus.db"))
    db.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO demo (value) VALUES ('ok')")
    db.commit()
    db.close()
    key_file = tmp_path / "backup.key"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-dir",
            str(data_dir),
            "--out",
            str(out_dir),
            "--key-file",
            str(key_file),
            "--keep",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    archives = list(out_dir.glob("nexus-*.tar.gz.enc"))
    assert len(archives) == 1
    assert not list(out_dir.glob("nexus-*.tar.gz"))
    payload = Fernet(key_file.read_bytes()).decrypt(archives[0].read_bytes())
    assert payload.startswith(b"\x1f\x8b")
