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
import os
import secrets
import sqlite3
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response, status

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
                updated_at    REAL NOT NULL
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


def require_auth(request: Request) -> dict[str, Any]:
    """Dependency de FastAPI: exige sesión válida y CSRF en métodos mutantes.

    Adjunta la sesión a ``request.state.session`` para que los endpoints
    conozcan al actor (usuario) sin volver a consultar la base de datos.
    """
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE)
    session = get_session(token, settings.SESSION_IDLE_MINUTES)
    if session is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "No autenticado. Inicia sesión en el panel."
        )
    if request.method in _UNSAFE_METHODS:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not hmac.compare_digest(csrf, session["csrf"]):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Token CSRF ausente o inválido."
            )
    request.state.session = session
    return session
