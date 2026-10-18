"""API de seguridad: tokens de acceso, sesiones abiertas y segundo factor.

Se registra en ``main.py`` con ``require_auth``, igual que el resto de la API
privada:

* Los **tokens de API** (``Authorization: Bearer nx_...``) también acceden: un
  token ``read`` puede listar tokens y sesiones; crear o revocar tokens exige
  un token ``write`` (o la sesión del panel con su cabecera CSRF).
* El secreto de un token **solo se devuelve al crearlo**; la lista nunca
  incluye el hash ni el token.
* El segundo factor (TOTP) pertenece a la cuenta del administrador y se gestiona
  solo desde su sesión de navegador (un token de API no debe poder desactivarlo).
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, status

from app.core import api_tokens, security, totp
from app.core.audit import audit
from app.schemas.models import (
    ActionResponse,
    ApiToken,
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenListResponse,
    SecuritySession,
    SecuritySessionListResponse,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorSetupResponse,
    TwoFactorStatusResponse,
)

router = APIRouter(tags=["seguridad"])


def _actor(request: Request) -> str:
    """Actor autenticado (usuario de la sesión o ``token:<nombre>``)."""
    return str(request.state.session["username"])


def _current_session_id(request: Request) -> str:
    """Identificador de la sesión que hace la petición (``''`` si va con token)."""
    return security.session_id(request.cookies.get(security.SESSION_COOKIE))


def _require_panel_session(request: Request) -> str:
    """Devuelve el usuario de la cuenta, exigiendo una sesión de navegador.

    El segundo factor protege la cuenta del administrador: gestionarlo con un
    token de API permitiría a una integración desactivarlo sin contraseña.
    """
    if request.state.session.get("auth_kind") != "session":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El segundo factor se gestiona desde la sesión del panel, "
            "no con un token de API.",
        )
    return str(request.state.session["username"])


# ============================ Tokens de API =============================
@router.get("/tokens", response_model=ApiTokenListResponse, summary="Lista los tokens de API")
async def list_api_tokens() -> ApiTokenListResponse:
    """Devuelve los tokens con sus metadatos (nunca el secreto ni su hash)."""
    items = api_tokens.list_tokens()
    return ApiTokenListResponse(count=len(items), tokens=[ApiToken(**item) for item in items])


@router.post(
    "/tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un token de API",
)
async def create_api_token(payload: ApiTokenCreate, request: Request) -> ApiTokenCreated:
    """Genera un token y lo devuelve **en claro una única vez**.

    Guárdalo inmediatamente: de la base de datos solo sale su SHA-256.
    """
    created = api_tokens.create(
        name=payload.name, scope=payload.scope, ttl_days=payload.ttl_days
    )
    audit(
        "security.token.created",
        actor=_actor(request),
        target=f"token:{created['info']['id']}",
        details={
            "nombre": payload.name,
            "ambito": payload.scope,
            "prefijo": created["info"]["prefix"],
            "caduca": created["info"]["expires_at"],
        },
    )
    return ApiTokenCreated(**created["info"], token=created["token"])


@router.delete(
    "/tokens/{token_id}", response_model=ActionResponse, summary="Revoca un token de API"
)
async def revoke_api_token(token_id: int, request: Request) -> ActionResponse:
    """Revoca el token: deja de autenticar de inmediato (la fila se conserva)."""
    info = api_tokens.get(token_id)
    if info is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Token de API no encontrado: {token_id}"
        )
    api_tokens.revoke(token_id)
    audit(
        "security.token.revoked",
        actor=_actor(request),
        target=f"token:{token_id}",
        details={"nombre": info["name"], "prefijo": info["prefix"]},
    )
    return ActionResponse(success=True, message=f"Token {info['name']} revocado.")


# ============================== Sesiones ================================
@router.get(
    "/security/sessions",
    response_model=SecuritySessionListResponse,
    summary="Lista las sesiones abiertas",
)
async def list_security_sessions(request: Request) -> SecuritySessionListResponse:
    """Inventario de sesiones con su antigüedad, inactividad y caducidad."""
    items = security.list_sessions(current_token=request.cookies.get(security.SESSION_COOKIE))
    return SecuritySessionListResponse(
        count=len(items), sessions=[SecuritySession(**item) for item in items]
    )


@router.delete(
    "/security/sessions/{session_id}",
    response_model=ActionResponse,
    summary="Revoca una sesión",
)
async def revoke_security_session(session_id: str, request: Request) -> ActionResponse:
    """Cierra una sesión concreta.

    **No se puede revocar la sesión actual**: invalidarla en mitad de la petición
    dejaría al panel sin sesión de forma confusa. Para salir está
    ``POST /api/auth/logout``.
    """
    current = _current_session_id(request)
    if current and hmac.compare_digest(session_id, current):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No se puede revocar la sesión actual: usa «Cerrar sesión» para salir.",
        )
    if not security.revoke_session(session_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "La sesión indicada no existe (¿ya estaba cerrada?).",
        )
    audit("security.session.revoked", actor=_actor(request), target=f"session:{session_id[:12]}")
    return ActionResponse(success=True, message="Sesión revocada.")


# ============================== 2FA (TOTP) ==============================
@router.get(
    "/security/2fa",
    response_model=TwoFactorStatusResponse,
    summary="Estado del segundo factor",
)
async def two_factor_status() -> TwoFactorStatusResponse:
    """Indica si la cuenta tiene 2FA activo y cuántos códigos de recuperación quedan."""
    return TwoFactorStatusResponse(**security.get_totp_state(security.admin_username()))


@router.post(
    "/security/2fa/setup",
    response_model=TwoFactorSetupResponse,
    summary="Inicia el alta del segundo factor",
)
async def two_factor_setup(request: Request) -> TwoFactorSetupResponse:
    """Genera el secreto TOTP y devuelve la URL ``otpauth://`` y los códigos de recuperación.

    Guarda el secreto **sin activarlo**: hasta que se confirme un código válido
    con ``/security/2fa/enable``, el login sigue funcionando con contraseña
    sola. Los códigos de recuperación se devuelven aquí una única vez (se
    guardan hasheados).
    """
    actor = _actor(request)
    username = _require_panel_session(request)
    secret = totp.generate_secret()
    codes = totp.generate_recovery_codes()
    security.stage_totp(username, secret, [totp.hash_recovery_code(code) for code in codes])
    audit("security.2fa.setup", actor=actor, details={"usuario": username})
    return TwoFactorSetupResponse(
        secret=secret,
        otpauth_url=totp.provisioning_uri(secret, username),
        recovery_codes=codes,
    )


@router.post(
    "/security/2fa/enable",
    response_model=ActionResponse,
    summary="Activa el segundo factor",
)
async def two_factor_enable(payload: TwoFactorEnableRequest, request: Request) -> ActionResponse:
    """Activa el 2FA tras comprobar un código de la app (ventana ±1 periodo)."""
    actor = _actor(request)
    username = _require_panel_session(request)
    if not security.get_totp_state(username)["configured"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No hay un segundo factor preparado: llama antes a /security/2fa/setup.",
        )
    if not security.verify_second_factor(username, payload.code)["ok"]:
        audit("security.2fa.enable", actor=actor, result="denied", details={"usuario": username})
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "El código de verificación no es válido."
        )
    security.enable_totp(username)
    audit("security.2fa.enabled", actor=actor, details={"usuario": username})
    return ActionResponse(
        success=True,
        message="Segundo factor activado. Se pedirá en el próximo inicio de sesión.",
    )


@router.post(
    "/security/2fa/disable",
    response_model=ActionResponse,
    summary="Desactiva el segundo factor",
)
async def two_factor_disable(payload: TwoFactorDisableRequest, request: Request) -> ActionResponse:
    """Desactiva el 2FA (exige la contraseña: perder el móvil no debe abrir el panel)."""
    actor = _actor(request)
    username = _require_panel_session(request)
    if not security.authenticate(username, payload.password):
        audit("security.2fa.disable", actor=actor, result="denied")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta."
        )
    security.disable_totp(username)
    audit("security.2fa.disabled", actor=actor, details={"usuario": username})
    return ActionResponse(success=True, message="Segundo factor desactivado.")


__all__ = ["router"]