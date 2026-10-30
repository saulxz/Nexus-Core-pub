"""Endpoints de autenticación, primer arranque y sesión del panel.

El router se registra SIN la dependencia global de autenticación: los
endpoints de login y setup deben ser accesibles sin sesión; el resto
(``/auth/session``, ``/auth/password``) validan la sesión explícitamente.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core import security, web_security
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
    """IP real del cliente (Cloudflare/XFF solo desde el proxy local)."""
    return web_security.client_ip(request)


def _open_session(
    request: Request, username: str, response: Response, *, reason: str
) -> AuthSessionResponse:
    """Crea la sesión, fija la cookie y devuelve el token CSRF."""
    settings = get_settings()
    token, csrf, _expires = security.create_session(
        username, settings.SESSION_IDLE_MINUTES, settings.SESSION_ABSOLUTE_HOURS
    )
    # Secure en cualquier host público (aunque cloudflared entregue HTTP al
    # origen) y en HTTPS; el panel local de pywebview sigue funcionando por HTTP.
    secure = (
        settings.SESSION_COOKIE_SECURE
        or web_security.is_https(request)
        or (settings.ENFORCE_HTTPS and not web_security.is_local_request(request))
    )
    security.set_session_cookie(
        response,
        token,
        max_age=int(settings.SESSION_ABSOLUTE_HOURS * 3600),
        secure=secure,
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
async def setup(
    payload: AuthSetupRequest, request: Request, response: Response
) -> AuthSessionResponse:
    """Crea el administrador con el token de configuración y abre sesión."""
    security.complete_setup(payload.token, payload.username, payload.password)
    return _open_session(request, payload.username.strip(), response, reason="setup")


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

    # --- Segundo factor (TOTP) ---
    # Solo se exige si la cuenta lo tiene activo. El límite de intentos ya está
    # registrado arriba, así que un ataque de fuerza bruta sobre los códigos
    # también acaba bloqueado por IP y por usuario.
    if security.get_totp_state(username)["enabled"]:
        if not payload.totp:
            audit("auth.login.2fa", actor=username, result="denied", details={"motivo": "sin código"})
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Esta cuenta tiene verificación en dos pasos: introduce el código "
                "de 6 dígitos (o uno de recuperación).",
            )
        outcome = security.verify_second_factor(username, payload.totp)
        if not outcome["ok"]:
            audit("auth.login.2fa", actor=username, result="denied", details={"ip": ip})
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "El código de verificación no es válido."
            )
        audit(
            "auth.login.2fa",
            actor=username,
            details={
                "metodo": outcome["method"],
                "codigos_restantes": outcome["recovery_left"],
            },
        )

    security.clear_login_attempts(user_key)
    audit("auth.login", actor=username, result="ok", details={"ip": ip})
    return _open_session(request, username, response, reason="login")


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
