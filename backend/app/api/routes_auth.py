"""Endpoints de autenticación, primer arranque y sesión del panel.

El router se registra SIN la dependencia global de autenticación: los
endpoints de login y setup deben ser accesibles sin sesión; el resto
(``/auth/session``, ``/auth/password``) validan la sesión explícitamente.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core import security
from app.core.audit import audit
from app.core.config import get_settings
from app.schemas.models import (
    AuthLoginRequest,
    AuthPasswordChangeRequest,
    AuthSessionResponse,
    AuthSetupRequest,
    AuthSetupStatusResponse,
)

router = APIRouter(tags=["auth"])


def _client_ip(request: Request) -> str:
    """IP del cliente (para la limitación de intentos y la auditoría)."""
    return request.client.host if request.client else "desconocida"


def _open_session(username: str, response: Response, *, reason: str) -> AuthSessionResponse:
    """Crea la sesión, fija la cookie y devuelve el token CSRF."""
    settings = get_settings()
    token, csrf, _expires = security.create_session(
        username, settings.SESSION_IDLE_MINUTES, settings.SESSION_ABSOLUTE_HOURS
    )
    security.set_session_cookie(
        response,
        token,
        max_age=int(settings.SESSION_ABSOLUTE_HOURS * 3600),
        secure=settings.SESSION_COOKIE_SECURE,
    )
    audit("auth.session.created", actor=username, details={"motivo": reason})
    return AuthSessionResponse(authenticated=True, username=username, csrf=csrf)


@router.get(
    "/auth/setup-status",
    response_model=AuthSetupStatusResponse,
    summary="Estado del primer arranque",
)
async def setup_status() -> AuthSetupStatusResponse:
    """Indica si el panel necesita completar el asistente de configuración."""
    return AuthSetupStatusResponse(needs_setup=not security.has_admin())


@router.post(
    "/auth/setup",
    response_model=AuthSessionResponse,
    summary="Completa el primer arranque",
)
async def setup(payload: AuthSetupRequest, response: Response) -> AuthSessionResponse:
    """Crea el administrador con el token de configuración y abre sesión."""
    security.complete_setup(payload.token, payload.username, payload.password)
    return _open_session(payload.username.strip(), response, reason="setup")


@router.post("/auth/login", response_model=AuthSessionResponse, summary="Inicia sesión")
async def login(
    payload: AuthLoginRequest, request: Request, response: Response
) -> AuthSessionResponse:
    """Autentica al administrador con limitación de intentos por usuario y por IP."""
    settings = get_settings()
    ip = _client_ip(request)
    username = payload.username.strip()
    user_key = f"user:{ip}:{username.lower()}"
    ip_key = f"ip:{ip}"

    allowed_user, retry_user = security.check_login_rate(
        user_key, settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_WINDOW_SECONDS
    )
    allowed_ip, retry_ip = security.check_login_rate(
        ip_key, settings.LOGIN_RATE_LIMIT * 4, settings.LOGIN_RATE_WINDOW_SECONDS
    )
    if not allowed_user or not allowed_ip:
        retry_after = max(retry_user, retry_ip)
        audit("auth.login", actor=username, result="throttled", details={"ip": ip})
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Demasiados intentos fallidos. Espera {retry_after} segundos.",
            headers={"Retry-After": str(retry_after)},
        )

    security.record_login_attempt(user_key)
    security.record_login_attempt(ip_key)

    if not security.authenticate(username, payload.password):
        audit("auth.login", actor=username, result="denied", details={"ip": ip})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos."
        )

    security.clear_login_attempts(user_key)
    audit("auth.login", actor=username, result="ok", details={"ip": ip})
    return _open_session(username, response, reason="login")


@router.post("/auth/logout", summary="Cierra la sesión")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Cierra la sesión actual y elimina la cookie del navegador."""
    settings = get_settings()
    token = request.cookies.get(security.SESSION_COOKIE)
    session = security.get_session(token, settings.SESSION_IDLE_MINUTES)
    if session:
        security.destroy_session(token)
        audit("auth.logout", actor=session["username"])
    security.clear_session_cookie(response)
    return {"success": True}


@router.get(
    "/auth/session", response_model=AuthSessionResponse, summary="Sesión actual"
)
async def session_info(request: Request) -> AuthSessionResponse:
    """Devuelve la sesión activa (401 si no hay) junto al token CSRF."""
    session = security.require_auth(request)
    return AuthSessionResponse(
        authenticated=True, username=session["username"], csrf=session["csrf"]
    )


@router.post("/auth/password", summary="Cambia la contraseña")
async def change_password(
    payload: AuthPasswordChangeRequest, request: Request
) -> dict[str, bool]:
    """Cambia la contraseña e invalida el resto de sesiones abiertas."""
    session = security.require_auth(request)  # exige CSRF en POST
    if not security.authenticate(session["username"], payload.current_password):
        audit("auth.password.change", actor=session["username"], result="denied")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta."
        )
    security.validate_password(payload.new_password)
    security.update_password(session["username"], payload.new_password)
    security.destroy_all_sessions(
        except_token=request.cookies.get(security.SESSION_COOKIE)
    )
    audit("auth.password.changed", actor=session["username"])
    return {"success": True}
