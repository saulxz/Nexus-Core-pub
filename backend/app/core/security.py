"""Autenticación, sesiones y CSRF del panel de administración.

Diseño (un único administrador, despliegue inicial en LAN):

* Contraseñas con **argon2id** (``argon2-cffi``).
* Sesiones *server-side* en SQLite: se genera un token aleatorio de 256 bits
  y en la base de datos solo se almacena su SHA-256. El token viaja en una
  cookie ``HttpOnly`` + ``SameSite=Strict`` (y ``Secure`` cuando hay HTTPS).
* Doble expiración: por inactividad (``SESSION_IDLE_MINUTES``) y absoluta
  (``SESSION_ABSOLUTE_HOURS``).
* **CSRF**: cada sesión lleva un token asociado que el frontend debe enviar
  en la cabecera ``X-CSRF-Token`` en todo método mutante.
* Primer arranque: si no existe administrador se genera un token de setup en
  ``$NEXUS_DATA_DIR/setup_token`` (0600) que el asistente web exige para
  crear la cuenta. No hay credenciales por defecto.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response, status

from app.core import totp
from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.core.paths import db_path, setup_token_path

SESSION_COOKIE = "nexus_session"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_TOUCH_INTERVAL_SECONDS = 60.0

_hasher = PasswordHasher()


def _utc() -> float:
    return time.time()


def connect() -> sqlite3.Connection:
    """Abre una conexión SQLite con WAL y filas tipo diccionario."""
    conn = sqlite3.connect(str(db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Crea el esquema de autenticación y limitación de intentos."""
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                username      TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL,
                -- Segundo factor TOTP (RFC 6238). El secreto y los códigos de
                -- recuperación se guardan hasheados/cifrados por el módulo
                -- app.core.totp; aquí solo se persisten.
                totp_secret   TEXT,
                totp_enabled  INTEGER NOT NULL DEFAULT 0,
                totp_recovery TEXT,
                -- Último paso TOTP aceptado (anti-replay). Se guarda el
                -- contador de 30 s, no el código.
                totp_last_step INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash  TEXT PRIMARY KEY,
                csrf        TEXT NOT NULL,
                username    TEXT NOT NULL,
                created_at  REAL NOT NULL,
                last_seen   REAL NOT NULL,
                expires_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                id  INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                ts  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_attempts_key ON login_attempts(key, ts);
            """
        )
        # Migración idempotente para bases de datos creadas antes del anti-replay.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(admin)")}
        if "totp_last_step" not in columns:
            conn.execute("ALTER TABLE admin ADD COLUMN totp_last_step INTEGER")
        conn.commit()
    finally:
        conn.close()


# ============================== Contraseñas ==============================
def hash_password(password: str) -> str:
    """Devuelve el hash argon2id de la contraseña."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Comprueba una contraseña contra su hash (False ante cualquier error)."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True si el hash quedó obsoleto (parámetros de argon2 antiguos)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password(password: str) -> None:
    """Política mínima de contraseñas del panel."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña es demasiado larga."
        )


# ============================== Administrador ==============================
def has_admin() -> bool:
    """True si ya existe la cuenta de administrador."""
    conn = connect()
    try:
        row = conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone()
        return row is not None
    finally:
        conn.close()


def create_admin(username: str, password: str) -> None:
    """Crea la cuenta única de administrador (error si ya existe)."""
    now = _utc()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO admin (id, username, password_hash, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (username, hash_password(password), now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_password(username: str, password: str) -> None:
    """Actualiza la contraseña del administrador."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE admin SET password_hash = ?, updated_at = ? WHERE username = ?",
            (hash_password(password), _utc(), username),
        )
        conn.commit()
    finally:
        conn.close()


def authenticate(username: str, password: str) -> bool:
    """Verifica credenciales. Rehashea si los parámetros quedaron obsoletos."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT username, password_hash FROM admin WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        # Se ejecuta un hash "señuelo" para no filtrar por tiempos si el
        # usuario existe o no (mitigación de enumeración de usuarios).
        verify_password(hash_password("señuelo-de-tiempo-constante"), password)
        return False
    if not verify_password(row["password_hash"], password):
        return False
    if password_needs_rehash(row["password_hash"]):
        try:
            update_password(row["username"], password)
            logger.info("Hash de contraseña actualizado a parámetros actuales de argon2")
        except Exception as exc:  # noqa: BLE001 - nunca debe romper el login
            logger.warning("No se pudo rehashear la contraseña: {}", exc)
    return True


# ============================== Token de setup ==============================
def ensure_setup_token() -> str | None:
    """Devuelve el token de primer arranque (creándolo si hace falta).

    Devuelve ``None`` si ya existe administrador.
    """
    if has_admin():
        return None
    path = setup_token_path()
    if not path.exists():
        token = secrets.token_urlsafe(24)
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text(encoding="utf-8").strip()


def read_setup_token() -> str | None:
    """Lee el token de setup sin crearlo (para instaladores y tests)."""
    path = setup_token_path()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def complete_setup(token: str, username: str, password: str) -> None:
    """Completa el primer arranque creando la cuenta de administrador."""
    if has_admin():
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El administrador ya está configurado."
        )
    expected = read_setup_token()
    if not expected or not hmac.compare_digest(token.strip(), expected):
        audit("auth.setup", result="denied", details={"motivo": "token inválido"})
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "El token de configuración no es válido."
        )
    clean_username = username.strip()
    if not (3 <= len(clean_username) <= 64):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "El nombre de usuario debe tener entre 3 y 64 caracteres.",
        )
    validate_password(password)
    create_admin(clean_username, password)
    setup_token_path().unlink(missing_ok=True)
    audit("auth.setup", actor=clean_username, result="ok", details={"usuario": clean_username})


# ================================ Sesiones ================================
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_id(token: str | None) -> str:
    """Identificador público de una sesión a partir de su token en claro.

    Es el ``token_hash`` de la fila (la clave que necesita el revocador). **No
    es una credencial**: para autenticarse hay que presentar el token en claro,
    del que solo se conoce el resumen.
    """
    return _hash_token(token) if token else ""


def admin_username() -> str:
    """Usuario de la cuenta de administrador (la única que existe en el panel)."""
    conn = connect()
    try:
        row = conn.execute("SELECT username FROM admin WHERE id = 1").fetchone()
    finally:
        conn.close()
    return str(row["username"]) if row is not None else ""


def create_session(
    username: str, idle_minutes: int, absolute_hours: int
) -> tuple[str, str, float]:
    """Crea una sesión y devuelve ``(token, csrf, expira_en)``.

    El token solo existe en claro en este retorno (cookie); en la base de
    datos se guarda su hash SHA-256.
    """
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = _utc()
    expires_at = now + absolute_hours * 3600
    idle_limit = idle_minutes * 60
    conn = connect()
    try:
        # Limpieza oportunista de sesiones caducadas.
        conn.execute(
            "DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?",
            (now, now - idle_limit),
        )
        conn.execute(
            "INSERT INTO sessions (token_hash, csrf, username, created_at, last_seen, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_hash_token(token), csrf, username, now, now, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token, csrf, expires_at


def get_session(token: str | None, idle_minutes: int) -> dict[str, Any] | None:
    """Valida una sesión aplicando expiración absoluta y por inactividad."""
    if not token:
        return None
    now = _utc()
    token_hash = _hash_token(token)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        if now > row["expires_at"] or now - row["last_seen"] > idle_minutes * 60:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None
        if now - row["last_seen"] > _TOUCH_INTERVAL_SECONDS:
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            conn.commit()
        return {
            "username": row["username"],
            "csrf": row["csrf"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
    finally:
        conn.close()


def destroy_session(token: str | None) -> None:
    """Elimina una sesión concreta."""
    if not token:
        return
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()
    finally:
        conn.close()


def destroy_all_sessions(except_token: str | None = None) -> None:
    """Invalida todas las sesiones (p. ej. al cambiar la contraseña)."""
    conn = connect()
    try:
        if except_token:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash != ?",
                (_hash_token(except_token),),
            )
        else:
            conn.execute("DELETE FROM sessions")
        conn.commit()
    finally:
        conn.close()


def list_sessions(current_token: str | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Inventario de sesiones abiertas (la vista «seguridad» del panel).

    ``id`` es el ``token_hash`` de la fila: es la clave primaria que necesita
    :func:`revoke_session` y **no** es una credencial utilizable —para
    autenticarse habría que presentar el token en claro, del que solo se conoce
    el resumen—. Aun así se devuelve completo para que el revocador sea exacto.

    Args:
        current_token: Token en claro de la sesión que pregunta; su fila se
            marca con ``is_current`` para que la UI pueda protegerla.
        now: Instante de referencia para evaluar caducidades.

    Returns:
        Sesiones de la más reciente a la más antigua.
    """
    moment = _utc() if now is None else float(now)
    current_hash = _hash_token(current_token) if current_token else ""
    settings = get_settings()
    idle_limit = settings.SESSION_IDLE_MINUTES * 60
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM sessions ORDER BY last_seen DESC").fetchall()
    finally:
        conn.close()
    sessions: list[dict[str, Any]] = []
    for row in rows:
        last_seen = float(row["last_seen"])
        expires_at = float(row["expires_at"])
        sessions.append(
            {
                "id": str(row["token_hash"]),
                "username": str(row["username"]),
                "created_at": float(row["created_at"]),
                "last_seen": last_seen,
                "expires_at": expires_at,
                "idle_seconds": round(max(0.0, moment - last_seen), 1),
                "idle_limit_seconds": idle_limit,
                "expired": moment > expires_at or moment - last_seen > idle_limit,
                "is_current": bool(current_hash) and str(row["token_hash"]) == current_hash,
            }
        )
    return sessions


def revoke_session(session_id: str) -> bool:
    """Elimina una sesión por su ``token_hash`` (``False`` si no existía)."""
    conn = connect()
    try:
        removed = conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (str(session_id),)
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(removed)


# ====================== Segundo factor (TOTP) =========================
def get_totp_state(username: str) -> dict[str, Any]:
    """Estado del segundo factor de una cuenta.

    Returns:
        ``{"enabled": bool, "configured": bool, "recovery_left": int}``.
        ``configured`` indica que hay un secreto guardado aunque todavía no se
        haya activado (fase de setup pendiente de confirmar).
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT totp_secret, totp_enabled, totp_recovery FROM admin WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"enabled": False, "configured": False, "recovery_left": 0}
    recovery = _recovery_hashes(row["totp_recovery"])
    return {
        "enabled": bool(row["totp_enabled"]) and bool(row["totp_secret"]),
        "configured": bool(row["totp_secret"]),
        "recovery_left": len(recovery),
    }


def _recovery_hashes(raw: str | None) -> list[str]:
    """Deserializa la lista JSON de códigos de recuperación hasheados."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def stage_totp(username: str, secret: str, recovery_hashes: list[str]) -> None:
    """Guarda el secreto TOTP **sin activarlo** y los códigos hasheados.

    Es la fase de *setup*: el operador escanea el QR y confirma con un código.
    Hasta que llama a :func:`enable_totp` el segundo factor no se exige al
    iniciar sesión (así un setup a medias no deja la cuenta inaccesible).
    """
    conn = connect()
    try:
        conn.execute(
            "UPDATE admin SET totp_secret = ?, totp_enabled = 0, totp_recovery = ?, "
            "totp_last_step = NULL WHERE username = ?",
            (secret, json.dumps(recovery_hashes, ensure_ascii=False), username),
        )
        conn.commit()
    finally:
        conn.close()


def enable_totp(username: str) -> None:
    """Activa el segundo factor (tras verificar un código correcto)."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE admin SET totp_enabled = 1, updated_at = ? WHERE username = ?",
            (_utc(), username),
        )
        conn.commit()
    finally:
        conn.close()


def disable_totp(username: str) -> None:
    """Desactiva y borra el segundo factor (secret y códigos de recuperación)."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE admin SET totp_secret = NULL, totp_enabled = 0, "
            "totp_recovery = NULL, totp_last_step = NULL, updated_at = ? "
            "WHERE username = ?",
            (_utc(), username),
        )
        conn.commit()
    finally:
        conn.close()


def verify_second_factor(username: str, code: str, now: float | None = None) -> dict[str, Any]:
    """Valida un segundo factor (código TOTP o de recuperación).

    Un **código de recuperación** se consume: si coincide con alguno de los
    hasheados se elimina y no vuelve a servir (uso único).

    Los códigos **TOTP** llevan **anti-replay**: se guarda el contador de 30 s
    del último código aceptado (``admin.totp_last_step``) y se rechaza cualquier
    paso menor o igual. Así, un código interceptado no se puede reutilizar
    dentro de su ventana de validez. La tolerancia ±1 periodo sigue activa para
    absorber el desfase del reloj; el precio es que un código del periodo
    anterior al último aceptado también se rechaza.

    Returns:
        ``{"ok": bool, "method": "totp" | "recovery" | None,
        "recovery_left": int, "reason": str | None}``.
    """
    moment = _utc() if now is None else float(now)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT totp_secret, totp_recovery, totp_last_step "
            "FROM admin WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None or not row["totp_secret"]:
            return {"ok": False, "method": None, "recovery_left": 0, "reason": None}

        secret = str(row["totp_secret"])
        step = totp.verify_step(secret, code, timestamp=moment)
        if step is not None:
            last_step = row["totp_last_step"]
            state = get_totp_state(username)
            if last_step is not None and step <= int(last_step):
                return {
                    "ok": False,
                    "method": None,
                    "recovery_left": int(state["recovery_left"]),
                    "reason": "replay",
                }
            conn.execute(
                "UPDATE admin SET totp_last_step = ? WHERE username = ?",
                (step, username),
            )
            conn.commit()
            return {
                "ok": True,
                "method": "totp",
                "recovery_left": int(state["recovery_left"]),
                "reason": None,
            }

        # Segundo intento: código de recuperación (uso único, comparación constante).
        stored = _recovery_hashes(row["totp_recovery"])
        digest = totp.hash_recovery_code(code)
        match = next((item for item in stored if hmac.compare_digest(item, digest)), None)
        if match is None:
            return {
                "ok": False,
                "method": None,
                "recovery_left": len(stored),
                "reason": None,
            }
        remaining = [item for item in stored if item != match]
        conn.execute(
            "UPDATE admin SET totp_recovery = ? WHERE username = ?",
            (json.dumps(remaining, ensure_ascii=False), username),
        )
        conn.commit()
        return {
            "ok": True,
            "method": "recovery",
            "recovery_left": len(remaining),
            "reason": None,
        }
    finally:
        conn.close()


# ========================= Limitación de intentos =========================
def check_login_rate(
    key: str, limit: int, window_seconds: int
) -> tuple[bool, int]:
    """Comprueba si ``key`` superó ``limit`` intentos en la ventana.

    Devuelve ``(permitido, segundos_de_espera)``.
    """
    now = _utc()
    conn = connect()
    try:
        conn.execute("DELETE FROM login_attempts WHERE ts < ?", (now - window_seconds,))
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM login_attempts "
            "WHERE key = ? AND ts >= ?",
            (key, now - window_seconds),
        ).fetchone()
        conn.commit()
        if row["n"] >= limit:
            oldest = row["oldest"] or now
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, retry_after
        return True, 0
    finally:
        conn.close()


def record_login_attempt(key: str) -> None:
    """Registra un intento de login para la ventana de limitación."""
    conn = connect()
    try:
        conn.execute("INSERT INTO login_attempts (key, ts) VALUES (?, ?)", (key, _utc()))
        conn.commit()
    finally:
        conn.close()


def clear_login_attempts(key: str) -> None:
    """Resetea los intentos tras un login correcto."""
    conn = connect()
    try:
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


# ============================ Cookie y dependencia ============================
def set_session_cookie(response: Response, token: str, max_age: int, secure: bool) -> None:
    """Fija la cookie de sesión (HttpOnly + SameSite=Strict)."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Elimina la cookie de sesión del navegador."""
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def _bearer_token(request: Request) -> str | None:
    """Extrae el token de API de la cabecera ``Authorization: Bearer ...``."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    value = header[7:].strip()
    return value or None


def _token_session(request: Request, raw: str) -> dict[str, Any] | None:
    """Construye la sesión equivalente a partir de un token de API.

    Devuelve ``None`` si el token no es válido (inexistente, revocado o
    caducado).

    Los tokens de API **no** llevan CSRF (no dependen de una cookie que un
    navegador adjunte automáticamente: si existiera, bastaría un formulario
    externo para que el navegador falsificara la cabecera *Authorization* que el
    panel tiene permitted por CORS). Es el mismo modelo que un JWT o una clave
    de API: quien la posee es quien la usa.
    """
    # Import local: app.core.db (y por tanto este módulo) se importa desde
    # api_tokens, así que una importación a nivel de módulo sería circular.
    from app.core import api_tokens

    info = api_tokens.authenticate(raw)
    if info is None:
        logger.warning("Rechazado un token de API inválido, revocado o caducado")
        return None
    return {
        "username": f"token:{info['name']}",
        "csrf": "",
        "auth_kind": "token",
        "scope": info["scope"],
        "token_id": info["id"],
        "created_at": info["created_at"],
        "expires_at": info["expires_at"],
        "is_current": False,
    }


def _session_session(request: Request) -> dict[str, Any] | None:
    """Valida la sesión por cookie y exige CSRF en los métodos mutantes."""
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE)
    session = get_session(token, settings.SESSION_IDLE_MINUTES)
    if session is None:
        return None
    if request.method in _UNSAFE_METHODS:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not hmac.compare_digest(csrf, session["csrf"]):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Token CSRF ausente o inválido."
            )
    session.update({"auth_kind": "session", "scope": "write", "is_current": True})
    return session


def _authenticate(request: Request, *, require_write: bool) -> dict[str, Any] | None:
    """Resuelve la identidad de la petición: token de API o sesión con cookie.

    Si la petición trae ``Authorization: Bearer``, esa credencial es **la única**
    que se considera: un token inválido produce 401 en lugar de caer de vuelta
    a la cookie. Sin ese «no degradar», un token caducado o revocado seguiría
    funcionando para cualquiera que además tuviera la sesión abierta, y el
    cliente no vería ningún error de su credencial.

    Args:
        request: Petición entrante.
        require_write: Si es ``True``, un token de API debe tener ámbito
            ``write`` para métodos mutantes.

    Returns:
        La sesión normalizada, o ``None`` si no hay ninguna credencial válida.
    """
    raw = _bearer_token(request)
    if raw is not None:
        session = _token_session(request, raw)
        if session is not None and require_write and request.method in _UNSAFE_METHODS:
            if session["scope"] != "write":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "El token de API no tiene permiso para modificar el panel "
                    "(ámbito 'read'). Crea uno con ámbito 'write'.",
                )
        return session
    return _session_session(request)


def require_auth(request: Request) -> dict[str, Any]:
    """Dependency de FastAPI: exige sesión válida (o token de API) y CSRF.

    Acepta dos credenciales:

    * **Cookie de sesión + cabecera ``X-CSRF-Token``** en los métodos mutantes
      (el frontend del panel).
    * **``Authorization: Bearer <token>``** con un token de API: no necesita
      CSRF y, para mutar, exige ámbito ``write``.

    En ambos casos la identidad se adjunta a ``request.state.session`` para que
    los endpoints conozcan al actor sin volver a consultar la base de datos.
    """
    session = _authenticate(request, require_write=True)
    if session is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "No autenticado. Inicia sesión en el panel."
        )
    request.state.session = session
    return session


def require_read_auth(request: Request) -> dict[str, Any]:
    """Dependency como :func:`require_auth` pero sin exigir ámbito ``write``.

    Se reserva para transportes que usan un método mutante para una operación
    lógicamente de **solo lectura** (el servidor MCP, que es un ``POST`` con
    JSON-RPC). Un token ``read`` basta ahí; la sesión por cookie sigue exigiendo
    CSRF, para que el endpoint no se convierta en un agujero de CSRF.
    """
    session = _authenticate(request, require_write=False)
    if session is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "No autenticado. Inicia sesión en el panel."
        )
    request.state.session = session
    return session
