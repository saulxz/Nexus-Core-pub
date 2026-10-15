"""Control de servicios del sistema (FASE 2).

Todas las acciones mutantes pasan por ``sudo -n svc-ctl <acción> <unidad>``:
el helper (propiedad de root) valida la acción contra su lista blanca y la
unidad contra ``/etc/nexus/allowed-units`` antes de invocar systemctl.
Cada acción queda registrada en la auditoría.
"""

from __future__ import annotations

from typing import Any

from app.core.audit import audit
from app.core.privileged import available, run_helper
from app.modules import systemd_manager

ALLOWED_ACTIONS = ("start", "stop", "restart", "reload")


def control(unit: str, action: str, actor: str) -> dict[str, Any]:
    """Ejecuta una acción de control sobre un servicio, con auditoría.

    Devuelve ``{"success", "message", "service"}`` donde ``service`` es el
    estado actualizado de la unidad (o None si no se pudo actuar).
    """
    try:
        normalized = systemd_manager.normalize_unit(unit)
    except ValueError as exc:
        return {"success": False, "message": str(exc), "service": None}

    action = (action or "").strip().lower()
    if action not in ALLOWED_ACTIONS:
        return {
            "success": False,
            "message": (
                f"Acción no permitida: '{action}'. "
                f"Válidas: {', '.join(ALLOWED_ACTIONS)}."
            ),
            "service": None,
        }

    if not available():
        return {
            "success": False,
            "message": (
                "El control de servicios requiere Linux con systemd y los "
                "helpers instalados (deploy/install.sh)."
            ),
            "service": None,
        }

    result = run_helper("svc-ctl", [action, normalized], timeout=60)
    if result["success"]:
        message = f"Acción '{action}' ejecutada sobre {normalized}."
    else:
        message = (
            result["output"]
            or f"El helper devolvió el código {result['code']}."
        )

    audit(
        "service.action",
        actor=actor,
        target=normalized,
        result="ok" if result["success"] else "fail",
        details={"accion": action, "salida": result["output"][-300:]},
    )

    detail = systemd_manager.get_service(normalized) if result["success"] else None
    return {"success": result["success"], "message": message, "service": detail}
