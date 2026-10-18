"""Autorremediación *con supervisión humana* (FASE 4).

Cuando una alerta se abre, el motor puede **proponer** una acción que la resuelva
(dis cleaning, restart...). La propuesta se registra con el **mismo mecanismo que
usa el agente de IA** (:mod:`app.ai.pending`): queda visible en el panel y solo
se ejecuta cuando una persona autenticada la confirma.

Decisiones de diseño (por qué esto no es «auto-fix»):

* **Nunca se ejecuta nada solo.** Este módulo no llama a Docker ni a systemd:
  solo escribe en ``pending_actions``. La ejecución ocurre en el endpoint de
  confirmación, que ya existía para el agente de IA.
* **Opt-in por regla.** Sin ``alert_rules.remediation_action`` no se propone
  nada; el valor se valida contra :data:`REMEDIATION_ACTIONS`.
* **Objetivo explícito.** Las acciones que necesitan un destinatario
  (``container_restart``, ``service_restart``) lo toman del *nombre de la regla*,
  que debe ser un identificador válido. Si no lo es, no se propone nada y se
  registra el motivo: es preferible no proponer a proponer sobre el objetivo
  equivocado.
* La propuesta caduca como cualquier propuesta del agente (TTL) y es de un solo
  uso.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai import pending
from app.core.audit import audit
from app.core.logging_conf import logger

#: Acciones de remediación admitidas por una regla de alerta.
REMEDIATION_ACTIONS: tuple[str, ...] = (
    "docker_prune_images",
    "container_restart",
    "service_restart",
)

#: Nombre de un contenedor/nombre de unidad válido como objetivo de la acción.
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

#: Tiempo que una propuesta de remediación permanece confirmable (s).
TTL_SECONDS = 900


def _args_for(action: str, rule: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Argumentos de la propuesta según la acción.

    Returns:
        ``(argumentos, resumen)`` o ``None`` si el objetivo de la regla no es
        válido (no se propone nada en ese caso).
    """
    name = str(rule.get("name") or "").strip()
    if action == "docker_prune_images":
        return (
            {"images": True, "volumes": False, "containers": False},
            f"Eliminación de imágenes Docker sin usar (disparada por la regla «{name}»)",
        )

    if action == "container_restart":
        if not _TARGET_RE.match(name):
            return None
        return (
            {"contenedor": name},
            f"Reinicio del contenedor Docker «{name}»",
        )

    if action == "service_restart":
        unit = name if name.endswith(".service") else f"{name}.service"
        if not _TARGET_RE.match(unit):
            return None
        return ({"unidad": unit, "accion": "restart"}, f"Reinicio del servicio «{unit}»")

    return None


def propose(event: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any] | None:
    """Crea la propuesta de remediación de una alerta recién abierta.

    Args:
        event: Fila de ``alert_events`` que se acaba de abrir.
        rule: Fila de ``alert_rules`` que la originó.

    Returns:
        La propuesta creada, o ``None`` si la regla no tiene remediación, la
        acción no es válida o el objetivo no lo es.
    """
    action = str(rule.get("remediation_action") or "").strip()
    if not action:
        return None
    if action not in REMEDIATION_ACTIONS:
        logger.warning(
            "Regla {}: acción de remediación no permitida '{}' (se ignora).",
            rule.get("id"),
            action,
        )
        return None

    resolved = _args_for(action, rule)
    if resolved is None:
        logger.warning(
            "Regla {}: el nombre '{}' no sirve como objetivo de '{}'; no se propone nada.",
            rule.get("id"),
            rule.get("name"),
            action,
        )
        audit(
            "remediation.skipped",
            target=f"rule:{rule.get('id')}",
            result="denied",
            details={"accion": action, "motivo": "objetivo no válido"},
        )
        return None

    args, summary = resolved
    proposal = pending.create(
        action,
        {**args, "evento": event.get("id"), "regla": rule.get("id")},
        summary,
        ttl=TTL_SECONDS,
        actor=pending.SYSTEM_ACTOR,
    )
    audit(
        "remediation.proposed",
        target=f"event:{event.get('id')}",
        details={
            "accion": action,
            "regla": rule.get("id"),
            "accion_id": proposal["id"],
            "resumen": summary,
        },
    )
    logger.info(
        "Propuesta de remediación creada para el evento {}: {}",
        event.get("id"),
        summary,
    )
    return proposal


def open_proposals(limit: int = 20) -> list[dict[str, Any]]:
    """Propuestas de remediación aún pendientes de confirmación."""
    proposals = pending.list_by_tools(REMEDIATION_ACTIONS, limit=limit)
    open_items: list[dict[str, Any]] = []
    for item in proposals:
        # list_by_tools devuelve las más recientes; get() marca las caducadas.
        stored = pending.get(item["id"]) or item
        if stored.get("status") != "pending":
            continue
        args = stored.get("args") or {}
        open_items.append(
            {
                "id": stored["id"],
                "rule_id": args.get("regla"),
                "rule_name": "",
                "action": stored["tool"],
                "summary": stored["summary"],
                "event_id": args.get("evento"),
                "args": args,
                "status": stored["status"],
                "created_at": stored["created_at"],
                "expires_at": stored["expires_at"],
            }
        )

    from app.modules import alerts  # importación local: evita un ciclo con alerts

    for item in open_items:
        rule_id = item.get("rule_id")
        if rule_id is None:
            continue
        rule = alerts.get_rule(int(rule_id))
        if rule:
            item["rule_name"] = str(rule.get("name") or "")
    return open_items


__all__ = ["REMEDIATION_ACTIONS", "TTL_SECONDS", "open_proposals", "propose"]