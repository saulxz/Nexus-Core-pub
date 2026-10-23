"""API de la auditoría de seguridad de solo lectura.

* ``GET /api/audit/security`` devuelve el informe cacheado (5 min) o lo genera.
* ``POST /api/audit/security/run`` fuerza una nueva ejecución.
* ``POST /api/audit/security/baseline`` guarda el informe actual como baseline.
* ``DELETE /api/audit/security/baseline`` borra el baseline.

El router se registra con ``require_auth`` en ``main.py``; los POST/DELETE
exigen el CSRF de la sesión (o un token de API con ámbito write). La auditoría
no escribe en el sistema: guardar el baseline es una acción explícita del
usuario y solo crea un JSON 0600 en el directorio de datos.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.modules.security_audit import (
    compare,
    delete_baseline,
    load_baseline,
    run_audit,
    save_baseline,
)
from app.schemas.security_audit import (
    SecurityAuditBaselineDeleteResponse,
    SecurityAuditBaselineResponse,
    SecurityAuditResponse,
)

router = APIRouter(tags=["auditoria"])


def _with_comparison(data: dict) -> dict:
    result = dict(data)
    result["comparison"] = compare(result, load_baseline())
    return result


@router.get(
    "/audit/security",
    response_model=SecurityAuditResponse,
    summary="Auditoría de seguridad del host (solo lectura)",
)
async def get_security_audit() -> SecurityAuditResponse:
    """Devuelve el informe cacheado o ejecuta la auditoría si no hay caché."""
    data = await asyncio.to_thread(run_audit, False)
    return SecurityAuditResponse(**_with_comparison(data))


@router.post(
    "/audit/security/run",
    response_model=SecurityAuditResponse,
    summary="Fuerza una nueva auditoría de seguridad",
)
async def run_security_audit() -> SecurityAuditResponse:
    """Ignora la caché y vuelve a analizar el host."""
    data = await asyncio.to_thread(run_audit, True)
    return SecurityAuditResponse(**_with_comparison(data))


@router.post(
    "/audit/security/baseline",
    response_model=SecurityAuditBaselineResponse,
    summary="Guarda el informe actual como baseline",
)
async def save_security_baseline() -> SecurityAuditBaselineResponse:
    """Ejecuta una pasada y guarda una fotografía para comparar después."""
    data = await asyncio.to_thread(run_audit, True)
    saved = save_baseline(data)
    return SecurityAuditBaselineResponse(ok=True, **saved)


@router.delete(
    "/audit/security/baseline",
    response_model=SecurityAuditBaselineDeleteResponse,
    summary="Borra el baseline de auditoría",
)
async def delete_security_baseline() -> SecurityAuditBaselineDeleteResponse:
    return SecurityAuditBaselineDeleteResponse(ok=True, removed=delete_baseline())
