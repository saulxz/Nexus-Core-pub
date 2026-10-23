"""Tests de seguridad, herramientas del sistema, MCP y autorremediación.

Cubre los bloques de la FASE 3/4:

* **Tokens de API**: alta, autenticación por ``Bearer``, salto de CSRF, ámbito
  ``read``/``write`` y revocación.
* **Sesiones**: listado y revocación (incluida la protección de la sesión actual).
* **2FA (TOTP)**: setup, activación, verificación en el login, código de
  recuperación de un solo uso y desactivación con reloj inyectado.
* **Herramientas del sistema**: degradación elegante en Windows (sin
  ``smartctl``/``apt-get``).
* **Diagnóstico**: el ZIP se descarga y **no contiene** un secreto plantado.
* **Docker**: la limpieza exige confirmación explícita.
* **FIM**: línea base, detección de cambios y diff; no acepta rutas arbitrarias.
* **MCP**: ``initialize``/``tools/list``/``tools/call`` con token de API.
* **Autorremediación**: una alerta con remediación crea una propuesta pendiente
  y **no** ejecuta nada.
"""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.core import api_tokens, security, totp
from app.core.paths import ai_env_path
from app.main import app
from app.modules import alerts, docker_manager, fim, remediation

TEST_USERNAME = "admin"
TEST_PASSWORD = "contrasena-de-prueba-123"


# ============================== Utilidades ==============================
@pytest.fixture
def anonymous_client() -> TestClient:
    """Cliente **sin sesión**.

    Las pruebas de login necesitan uno: un login correcto rota la cookie de
    sesión, lo que invalidaría el token CSRF que comparten el resto de tests.
    """
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    """Cabecera de autorización con un token de API."""
    return {"Authorization": f"Bearer {token}"}


def _new_token(name: str = "test", scope: str = "read", ttl_days: int | None = 90) -> str:
    """Crea un token directamente en el módulo (evita depender de la API)."""
    created = api_tokens.create(name=name, scope=scope, ttl_days=ttl_days)
    return str(created["token"])


# ============================== Tokens API ==============================
def test_token_create_returns_plaintext_once_and_hashes_it(client: TestClient, csrf: str) -> None:
    """El alta devuelve el secreto una vez y la base solo guarda su SHA-256."""
    response = client.post(
        "/api/tokens",
        json={"name": "portatil", "scope": "read", "ttl_days": 30},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    secret = body["token"]
    assert secret.startswith("nx_")
    assert body["prefix"] == secret[:8]
    assert "token_hash" not in body

    # El listado nunca devuelve el secreto.
    listing = client.get("/api/tokens", headers={"X-CSRF-Token": csrf})
    assert listing.status_code == 200
    entry = next(item for item in listing.json()["tokens"] if item["id"] == body["id"])
    assert entry["prefix"] == body["prefix"]
    assert secret not in json.dumps(listing.json())

    # En la base de datos está el hash, no el token.
    from app.core import db

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT token_hash FROM api_tokens WHERE id = ?", (body["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["token_hash"] == api_tokens.hash_token(secret)
    assert secret not in row["token_hash"]


def test_token_authenticates_and_skips_csrf(client: TestClient) -> None:
    """Un token válido autentica sin cookie y sin cabecera CSRF."""
    token = _new_token("mcp-test", scope="read")
    response = client.get("/api/tokens", headers=_auth(token))
    assert response.status_code == 200, response.text


def test_token_requires_write_scope_for_mutations(client: TestClient) -> None:
    """Un token «read» no puede mutar; uno «write» sí."""
    read_token = _new_token("solo-lectura", scope="read")
    denied = client.post("/api/tokens", json={"name": "x"}, headers=_auth(read_token))
    assert denied.status_code == 403
    assert "write" in denied.json()["detail"]

    write_token = _new_token("lectura-escritura", scope="write")
    allowed = client.post(
        "/api/tokens", json={"name": "desde-token", "scope": "read"}, headers=_auth(write_token)
    )
    assert allowed.status_code == 201, allowed.text


def test_token_revoke_stops_authentication(client: TestClient, csrf: str) -> None:
    """Tras revocar, el token deja de autenticar (401)."""
    created = client.post(
        "/api/tokens", json={"name": "temporal", "scope": "read"}, headers={"X-CSRF-Token": csrf}
    ).json()
    token = created["token"]
    assert client.get("/api/tokens", headers=_auth(token)).status_code == 200

    revoked = client.delete(f"/api/tokens/{created['id']}", headers={"X-CSRF-Token": csrf})
    assert revoked.status_code == 200, revoked.text
    assert client.get("/api/tokens", headers=_auth(token)).status_code == 401


def test_invalid_token_is_rejected(client: TestClient) -> None:
    """Un token ``Bearer`` inválido da 401 (no cae a la cookie de sesión)."""
    assert client.get("/api/tokens", headers=_auth("nx_inventado")).status_code == 401
    assert client.get("/api/tokens", headers=_auth("basura")).status_code == 401


def test_non_bearer_authorization_uses_the_session(client: TestClient) -> None:
    """Una cabecera ``Authorization`` que no es Bearer no invalida la sesión."""
    response = client.get("/api/tokens", headers={"Authorization": "Basico abc"})
    assert response.status_code == 200, response.text


def test_expired_token_does_not_authenticate(client: TestClient) -> None:
    """Un token con caducidad pasada deja de autenticar."""
    from app.core import db

    token = _new_token("caducado", ttl_days=1)
    digest = api_tokens.hash_token(token)
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE api_tokens SET expires_at = ? WHERE token_hash = ?",
            ("2000-01-01T00:00:00+00:00", digest),
        )
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/tokens", headers=_auth(token)).status_code == 401


# =============================== Sesiones ===============================
def test_sessions_listed_and_current_marked(client: TestClient, csrf: str) -> None:
    """El listado marca la sesión actual y expone su antigüedad."""
    response = client.get("/api/security/sessions", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 1
    current = [item for item in body["sessions"] if item["is_current"]]
    assert len(current) == 1
    assert current[0]["username"] == TEST_USERNAME
    assert current[0]["idle_limit_seconds"] > 0


def test_cannot_revoke_current_session(client: TestClient, csrf: str) -> None:
    """Revocar la sesión propia se rechaza con 409."""
    current = client.get("/api/security/sessions").json()["sessions"][0]
    assert current["is_current"] is True
    response = client.delete(
        f"/api/security/sessions/{current['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 409


def test_revoke_other_session(client: TestClient, csrf: str) -> None:
    """Una sesión secundaria se puede revocar y deja de validar."""
    other_token, other_csrf, _expires = security.create_session("otro", 30, 8)
    sessions = client.get("/api/security/sessions").json()["sessions"]
    target = next(item for item in sessions if item["username"] == "otro")
    assert target["is_current"] is False
    assert security.get_session(other_token, 30) is not None

    response = client.delete(
        f"/api/security/sessions/{target['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text
    assert security.get_session(other_token, 30) is None
    del other_csrf


# ================================ 2FA ==================================
def test_totp_setup_enable_verify_and_login(
    client: TestClient, csrf: str, anonymous_client: TestClient
) -> None:
    """Ciclo completo: setup → enable → login con TOTP → disable."""
    setup = client.post("/api/security/2fa/setup", headers={"X-CSRF-Token": csrf})
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["secret"] and body["otpauth_url"].startswith("otpauth://totp/")
    assert len(body["recovery_codes"]) == 10

    # Sin un código válido no se activa.
    wrong = client.post(
        "/api/security/2fa/enable", json={"code": "000000"}, headers={"X-CSRF-Token": csrf}
    )
    assert wrong.status_code == 400

    secret = body["secret"]
    code = totp.totp_code(secret)
    enabled = client.post(
        "/api/security/2fa/enable", json={"code": code}, headers={"X-CSRF-Token": csrf}
    )
    assert enabled.status_code == 200, enabled.text

    status = client.get("/api/security/2fa").json()
    assert status["enabled"] is True
    assert status["recovery_left"] == 10

    # El login pasa a exigir el segundo factor.
    without_code = anonymous_client.post(
        "/api/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert without_code.status_code == 401
    assert "dos pasos" in without_code.json()["detail"]

    with_code = anonymous_client.post(
        "/api/auth/login",
        json={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            # El enable ya consumió el código del periodo actual (anti-replay);
            # el login usa el siguiente paso para que sea un código nuevo.
            "totp": totp.totp_code(secret, time.time() + totp.PERIOD),
        },
    )
    assert with_code.status_code == 200, with_code.text

    disabled = client.post(
        "/api/security/2fa/disable",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert disabled.status_code == 200, disabled.text
    assert client.get("/api/security/2fa").json()["enabled"] is False


def test_totp_disable_requires_password(client: TestClient, csrf: str) -> None:
    """Desactivar el 2FA sin la contraseña correcta se rechaza."""
    response = client.post(
        "/api/security/2fa/disable",
        json={"password": "contrasena-equivocada"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_totp_window_accepts_adjacent_period() -> None:
    """El verificador tolera el desfase de un periodo del reloj del móvil."""
    secret = totp.generate_secret()
    base = 1_700_000_000
    assert totp.verify(secret, totp.totp_code(secret, base - totp.PERIOD), timestamp=base)
    assert totp.verify(secret, totp.totp_code(secret, base + totp.PERIOD), timestamp=base)
    assert not totp.verify(secret, totp.totp_code(secret, base + 5 * totp.PERIOD), timestamp=base)


def test_recovery_code_is_single_use(
    client: TestClient, csrf: str, anonymous_client: TestClient
) -> None:
    """Un código de recuperación sirve una vez y luego deja de valer."""
    setup = client.post("/api/security/2fa/setup", headers={"X-CSRF-Token": csrf}).json()
    code = totp.totp_code(setup["secret"])
    assert (
        client.post(
            "/api/security/2fa/enable", json={"code": code}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )

    recovery = setup["recovery_codes"][0]
    first = anonymous_client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD, "totp": recovery},
    )
    assert first.status_code == 200, first.text
    assert client.get("/api/security/2fa").json()["recovery_left"] == 9

    # El mismo código no sirve otra vez (queda invalidado al consumirse).
    second = TestClient(app).post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD, "totp": recovery},
    )
    assert second.status_code == 401

    client.post(
        "/api/security/2fa/disable",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )


def test_2fa_cannot_be_managed_with_api_token(client: TestClient) -> None:
    """Un token de API no puede tocar el segundo factor de la cuenta."""
    token = _new_token("gestor-2fa", scope="write")
    response = client.post("/api/security/2fa/setup", headers=_auth(token))
    assert response.status_code == 403


# ========================= Herramientas del sistema =====================
def test_system_tools_degrade_gracefully(client: TestClient, csrf: str) -> None:
    """Sin smartctl/apt-get (Windows) los endpoints degradan, no fallan."""
    smart = client.get("/api/system/smart", headers={"X-CSRF-Token": csrf})
    assert smart.status_code == 200, smart.text
    assert "available" in smart.json()
    if not smart.json()["available"]:
        assert smart.json()["reason"]
        assert smart.json()["disks"] == []

    updates = client.get("/api/system/updates", headers={"X-CSRF-Token": csrf})
    assert updates.status_code == 200, updates.text
    assert "available" in updates.json()
    if not updates.json()["available"]:
        assert updates.json()["reason"]

    ports = client.get("/api/system/ports", headers={"X-CSRF-Token": csrf})
    assert ports.status_code == 200, ports.text
    body = ports.json()
    assert isinstance(body["available"], bool)
    assert body["count"] == len(body["ports"])
    if not body["available"]:
        assert body["reason"]


# ============================= Diagnóstico ==============================
def test_diagnostics_zip_redacts_secrets(client: TestClient, csrf: str) -> None:
    """El ZIP de diagnóstico no contiene el secreto plantado."""
    secret = "sk-planted-SECRET-VALUE-000111222333"
    path = ai_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"AI_PROVIDER=openai\nOPENAI_API_KEY={secret}\n", encoding="utf-8")

    response = client.get("/api/system/diagnostics", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "manifest.json" in names
    assert any(name.startswith("config/") for name in names)

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["sensitive"] is True
    assert manifest["app"]["version"] == __version__
    assert "OPENAI_API_KEY" not in json.dumps(manifest)

    for name in names:
        assert secret.encode("utf-8") not in archive.read(name), f"secreto filtrado en {name}"

    # El fichero sigue en su sitio (el bundle no modifica nada).
    assert path.exists()


def test_diagnostics_audit_is_recorded(client: TestClient, csrf: str) -> None:
    """La descarga del paquete queda anotada en la auditoría."""
    from app.core.audit import read_events

    client.get("/api/system/diagnostics", headers={"X-CSRF-Token": csrf})
    events = read_events(limit=50, action="system.diagnostics.downloaded")["events"]
    assert events, "no se registró la descarga del diagnóstico"


# =============================== Docker =================================
def test_prune_requires_confirmation(client: TestClient, csrf: str) -> None:
    """Sin ``confirm: true`` la limpieza se rechaza con 400 y no se ejecuta."""
    denied = client.post(
        "/api/docker/prune", json={"images": True, "volumes": False}, headers={"X-CSRF-Token": csrf}
    )
    assert denied.status_code == 400
    assert "confirm" in denied.json()["detail"]

    accepted = client.post(
        "/api/docker/prune",
        json={"images": True, "volumes": False, "confirm": True},
        headers={"X-CSRF-Token": csrf},
    )
    # Sin daemon de Docker la operación degrada con un mensaje, pero la API
    # responde 200: la petición fue válida.
    assert accepted.status_code == 200, accepted.text
    assert "success" in accepted.json()


def test_prune_without_targets_is_refused(client: TestClient, csrf: str) -> None:
    """Confirmar sin indicar qué limpiar tampoco hace nada."""
    response = client.post(
        "/api/docker/prune",
        json={"images": False, "volumes": False, "confirm": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_docker_inventory_endpoints_answer(client: TestClient, csrf: str) -> None:
    """Los listados de Docker devuelven estructura aunque no haya daemon."""
    for path in ("/api/docker/volumes", "/api/docker/networks", "/api/docker/images"):
        response = client.get(path, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, f"{path}: {response.text}"
        assert "available" in response.json()


# ================================= FIM ==================================
def test_fim_status_and_baseline(client: TestClient, csrf: str) -> None:
    """El FIM compara con la línea base y muestra el diff de los cambios."""
    fim.snapshot(actor="test")

    created = client.post("/api/fim/baseline", json={}, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 200, created.text

    status = client.get("/api/fim/status", headers={"X-CSRF-Token": csrf}).json()
    assert status["count"] >= 4
    for item in status["files"]:
        assert item["path"] in fim.watched_paths()
        assert item["state"] in ("ok", "modificado", "ausente", "sin_baseline")

    # ai.env es uno de los ficheros vigilados: se modifica y se comprueba.
    path = ai_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("AI_PROVIDER=openai\n# línea añadida por el test\n", encoding="utf-8")
    changed = next(
        item
        for item in client.get("/api/fim/status").json()["files"]
        if item["path"] == str(path)
    )
    assert changed["state"] == "modificado"
    assert "línea añadida por el test" in changed["diff"]
    assert changed["extra_lines"] >= 1


def test_fim_ignores_paths_outside_the_allowlist(client: TestClient, csrf: str) -> None:
    """``POST /api/fim/baseline`` ignora cualquier ruta no vigilada."""
    response = client.post(
        "/api/fim/baseline",
        json={"paths": ["/etc/passwd", "C:/Windows/win.ini", str(ai_env_path())]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    written = response.json()["paths"]
    assert all(item in fim.watched_paths() for item in written)
    assert "/etc/passwd" not in written


def test_fim_baseline_is_audited(client: TestClient, csrf: str) -> None:
    """Actualizar la línea base queda en la cadena de auditoría."""
    from app.core.audit import read_events

    client.post("/api/fim/baseline", json={}, headers={"X-CSRF-Token": csrf})
    assert read_events(limit=50, action="fim.baseline.updated")["events"]


# ================================= MCP ==================================
def _rpc(client: TestClient, method: str, params: dict | None = None, token: str | None = None):
    """Envía un mensaje JSON-RPC al servidor MCP y devuelve la respuesta."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    headers = _auth(token) if token else {}
    return client.post("/api/mcp", json=payload, headers=headers)


def test_mcp_requires_authentication(anonymous_client: TestClient) -> None:
    """Sin sesión ni token, el servidor MCP no responde."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert anonymous_client.post("/api/mcp", json=payload).status_code == 401


def test_mcp_requires_csrf_from_a_browser_session(client: TestClient) -> None:
    """Con sesión de navegador (cookie), el POST sigue exigiendo CSRF."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/api/mcp", json=payload).status_code == 403


def test_mcp_initialize_and_tools_list_with_read_token(client: TestClient) -> None:
    """Un token de ámbito «read» puede usar el servidor MCP."""
    token = _new_token("mcp-read", scope="read")

    init = _rpc(client, "initialize", token=token)
    assert init.status_code == 200, init.text
    body = init.json()
    assert body["jsonrpc"] == "2.0"
    assert body["result"]["protocolVersion"]
    assert body["result"]["serverInfo"]["version"] == __version__

    listed = _rpc(client, "tools/list", token=token).json()
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"system_status", "metrics_summary", "list_monitors", "open_alerts", "fim_status"} <= names
    for tool in listed["result"]["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_mcp_tools_call_returns_content(client: TestClient) -> None:
    """``tools/call`` devuelve el resultado como contenido de texto JSON."""
    token = _new_token("mcp-call", scope="read")
    response = _rpc(client, "tools/call", {"name": "system_status", "arguments": {}}, token=token)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert "cpu_percent" in payload and "ram_percent" in payload


def test_mcp_unknown_method_and_invalid_json(client: TestClient) -> None:
    """Los errores de protocolo se devuelven como objetos JSON-RPC."""
    token = _new_token("mcp-err", scope="read")
    unknown = _rpc(client, "inventado", token=token).json()
    assert unknown["error"]["code"] == -32601

    bad = client.post(
        "/api/mcp", content=b"{no-es-json", headers={**_auth(token), "Content-Type": "application/json"}
    )
    assert bad.status_code == 200
    assert bad.json()["error"]["code"] == -32700


# ========================== Autorremediación ============================
@pytest.fixture
def remediation_rule() -> int:
    """Regla de disco con remediación por purga de imágenes (se borra al final)."""
    rule = alerts.create_rule(
        {
            "name": "disco-casi-lleno-test",
            "metric": "disk",
            "op": ">",
            "threshold": 1.0,
            "clear_threshold": 0.5,
            "duration_s": 0,
            "enabled": True,
            "remediation_action": "docker_prune_images",
        }
    )
    yield int(rule["id"])
    alerts.reset_state(int(rule["id"]))
    alerts.delete_rule(int(rule["id"]))


def test_alert_creates_pending_remediation_without_executing(
    client: TestClient, remediation_rule: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La alerta propone la remediación y NO ejecuta nada por su cuenta."""
    executed: list[bool] = []
    monkeypatch.setattr(
        docker_manager,
        "prune",
        lambda **kwargs: executed.append(True) or {"success": True, "message": "nope", "freed_bytes": 0},
    )

    alerts.reset_state()
    transitions = alerts.evaluate({"disk": 99.0}, now=time.time())
    opened = [item for item in transitions if item[0] == "opened"]
    assert opened, "la regla no abrió ningún evento"

    assert executed == [], "la remediación no debe ejecutarse automáticamente"

    proposals = client.get("/api/alerts/remediation").json()
    matching = [item for item in proposals["proposals"] if item["rule_id"] == remediation_rule]
    assert matching, "no se registró la propuesta de remediación"
    proposal = matching[0]
    assert proposal["action"] == "docker_prune_images"
    assert proposal["status"] == "pending"
    assert proposal["event_id"] == opened[0][1]["id"]

    from app.ai import pending

    stored = pending.get(proposal["id"])
    assert stored is not None and stored["status"] == "pending"


def test_rule_without_remediation_creates_no_proposal(
    client: TestClient, remediation_rule: int
) -> None:
    """Una regla sin ``remediation_action`` no propone nada."""
    alerts.update_rule(remediation_rule, {"remediation_action": None})
    alerts.reset_state()
    alerts.evaluate({"disk": 99.0}, now=time.time())
    proposals = client.get("/api/alerts/remediation").json()["proposals"]
    assert not [item for item in proposals if item["rule_id"] == remediation_rule]


def test_remediation_target_must_be_valid() -> None:
    """Con un nombre de regla inválido no se propone reiniciar nada."""
    assert remediation._args_for("container_restart", {"name": "sin espacios!"}) is None
    assert remediation._args_for("docker_prune_images", {"name": "cualquiera"}) is not None


def test_allowed_actions_are_validated(client: TestClient, csrf: str) -> None:
    """La API rechaza una acción de remediación fuera de la lista blanca."""
    response = client.post(
        "/api/alert-rules",
        json={
            "name": "regla-invalida",
            "metric": "disk",
            "op": ">",
            "threshold": 90,
            "remediation_action": "rm_-rf",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422, response.text


def test_valid_remediation_action_round_trips(client: TestClient, csrf: str) -> None:
    """Una acción válida se guarda en la regla y se devuelve en el listado."""
    created = client.post(
        "/api/alert-rules",
        json={
            "name": "disco-lleno",
            "metric": "disk",
            "op": ">",
            "threshold": 90,
            "clear_threshold": 80,
            "remediation_action": "docker_prune_images",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    try:
        assert created.json()["remediation_action"] == "docker_prune_images"
        rules = client.get("/api/alert-rules").json()["rules"]
        assert next(item for item in rules if item["id"] == rule_id)["remediation_action"] == (
            "docker_prune_images"
        )
    finally:
        client.delete(f"/api/alert-rules/{rule_id}", headers={"X-CSRF-Token": csrf})