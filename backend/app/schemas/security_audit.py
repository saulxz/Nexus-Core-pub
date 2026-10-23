"""Modelos de la auditoría de seguridad de solo lectura."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Status = Literal["fail", "warn", "pass", "unknown", "skipped"]


class SecurityAuditFinding(BaseModel):
    """Un hallazgo de la auditoría."""

    id: str = Field(..., description="Identificador estable del check.")
    category: str = Field(..., description="Bloque: Sistema, Identidad, Archivos, etc.")
    title: str = Field(..., description="Título legible.")
    severity: Severity = Field(..., description="Severidad del hallazgo.")
    status: Status = Field(..., description="Resultado del check.")
    evidence: str = Field(..., description="Evidencia observada (sin secretos).")
    recommendation: str = Field(..., description="Qué hacer para corregirlo.")
    reference: str = Field(default="", description="Referencia CIS/NIST/OWASP.")


class SecurityAuditResponse(BaseModel):
    """Informe completo de una ejecución."""

    read_only: bool = Field(..., description="La auditoría no modifica el sistema.")
    generated_at: str = Field(..., description="Fecha ISO-8601 UTC.")
    host: str = Field(..., description="Hostname auditado.")
    platform: str = Field(..., description="Sistema y release.")
    score: int = Field(..., ge=0, le=100, description="Puntuación 0-100.")
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="Conteo por severidad y estado (critical, high, pass, fail...).",
    )
    findings: list[SecurityAuditFinding] = Field(default_factory=list)
    comparison: dict[str, Any] | None = Field(
        default=None,
        description="Comparación con el baseline, si existe.",
    )
    duration_ms: int = Field(..., ge=0, description="Duración de la ejecución.")
    cached: bool = Field(..., description="True si se sirvió de la caché de 5 min.")


class SecurityAuditBaselineResponse(BaseModel):
    """Resultado de guardar un baseline."""

    ok: bool
    saved_at: str
    score: int
    findings: int


class SecurityAuditBaselineDeleteResponse(BaseModel):
    """Resultado de borrar el baseline."""

    ok: bool
    removed: bool
