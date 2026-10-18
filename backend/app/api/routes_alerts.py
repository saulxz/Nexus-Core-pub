"""API de alertas, métricas históricas y auditoría.

Endpoints del panel para:

* Reglas de alerta (CRUD) y eventos (listado, ``ack``, ``snooze``).
* Canales de notificación (CRUD + envío de prueba).
* Ventanas de mantenimiento (CRUD).
* Histórico de métricas (``/metrics-history`` y exportación CSV).
* Lectura del registro de auditoría encadenado.

Los datos están particionados por host (``host_id``, migración 5): el histórico
admite el host consultado y, por defecto, sigue siendo el del servidor local
(host 1), de modo que la vista actual no cambia.

El router se registra en ``main.py`` con la dependencia ``require_auth`` (sesión
válida + CSRF en los métodos mutantes), igual que el resto de la API privada, y
todas las acciones mutantes quedan anotadas en la cadena de auditoría.
"""

from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core import audit as audit_log
from app.core.audit import audit
from app.core.logging_conf import logger
from app.modules import alerts, metrics_store, notifications, remediation
from app.schemas.models import (
    ActionResponse,
    AlertEvent,
    AlertEventListResponse,
    AlertRule,
    AlertRuleCreate,
    AlertRuleListResponse,
    AlertRuleUpdate,
    AlertSnoozeRequest,
    AuditListResponse,
    ChannelTestResponse,
    MaintenanceWindow,
    MaintenanceWindowCreate,
    MaintenanceWindowListResponse,
    MaintenanceWindowUpdate,
    MetricsHistoryResponse,
    NotificationChannel,
    NotificationChannelCreate,
    NotificationChannelListResponse,
    NotificationChannelUpdate,
    RemediationListResponse,
    RemediationProposal,
)

router = APIRouter(tags=["alertas"])

#: Rangos de histórico admitidos por la API (cada uno usa su nivel de agregado).
RangeKey = Literal["1h", "24h", "7d", "30d"]


def _actor(request: Request) -> str:
    """Usuario autenticado (la dependencia global del router ya lo validó)."""
    return str(request.state.session["username"])


def _parse_metrics(raw: str) -> tuple[str, ...]:
    """Valida el parámetro ``metric=cpu,ram,temp`` de la API."""
    names = tuple(part.strip() for part in (raw or "").split(",") if part.strip())
    if not names:
        return ("cpu", "ram")
    unknown = [name for name in names if name not in metrics_store.METRIC_COLUMNS]
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Métrica desconocida: {', '.join(unknown)}. "
            f"Opciones: {', '.join(metrics_store.METRIC_COLUMNS)}.",
        )
    return names


# ========================== Reglas de alerta ==========================
@router.get(
    "/alert-rules",
    response_model=AlertRuleListResponse,
    summary="Lista las reglas de alerta",
)
async def list_alert_rules() -> AlertRuleListResponse:
    """Devuelve todas las reglas, habilitadas o no."""
    rules = alerts.list_rules()
    return AlertRuleListResponse(
        count=len(rules), rules=[AlertRule(**rule) for rule in rules]
    )


@router.post(
    "/alert-rules",
    response_model=AlertRule,
    status_code=status.HTTP_201_CREATED,
    summary="Crea una regla de alerta",
)
async def create_alert_rule(payload: AlertRuleCreate, request: Request) -> AlertRule:
    """Crea una regla y la registra en la auditoría."""
    rule = alerts.create_rule(payload.model_dump())
    audit("alert.rule.created", actor=_actor(request), target=f"rule:{rule['id']}")
    return AlertRule(**rule)


@router.patch(
    "/alert-rules/{rule_id}",
    response_model=AlertRule,
    summary="Actualiza una regla de alerta",
)
async def update_alert_rule(
    rule_id: int, payload: AlertRuleUpdate, request: Request
) -> AlertRule:
    """Actualiza los campos enviados (los ausentes se conservan)."""
    updated = alerts.update_rule(rule_id, payload.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Regla de alerta no encontrada: {rule_id}"
        )
    audit("alert.rule.updated", actor=_actor(request), target=f"rule:{rule_id}")
    return AlertRule(**updated)


@router.delete(
    "/alert-rules/{rule_id}",
    response_model=ActionResponse,
    summary="Elimina una regla de alerta",
)
async def delete_alert_rule(rule_id: int, request: Request) -> ActionResponse:
    """Elimina la regla y cierra sus eventos abiertos."""
    if not alerts.delete_rule(rule_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Regla de alerta no encontrada: {rule_id}"
        )
    audit("alert.rule.deleted", actor=_actor(request), target=f"rule:{rule_id}")
    return ActionResponse(success=True, message=f"Regla {rule_id} eliminada.")


# ========================== Eventos de alerta ==========================
@router.get(
    "/alert-events",
    response_model=AlertEventListResponse,
    summary="Lista los eventos de alerta",
)
async def list_alert_events(
    event_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(open|acked|closed)$",
        description="Filtra por estado del evento.",
    ),
    limit: int = Query(default=50, ge=1, le=500, description="Máximo de eventos a devolver."),
    since: str | None = Query(
        default=None, description="Solo eventos abiertos a partir de esta fecha (ISO8601)."
    ),
) -> AlertEventListResponse:
    """Devuelve los eventos, del más reciente al más antiguo."""
    events = alerts.list_events(status=event_status, limit=limit, since=since)
    return AlertEventListResponse(
        count=len(events), events=[AlertEvent(**event) for event in events]
    )


@router.post(
    "/alert-events/{event_id}/ack",
    response_model=AlertEvent,
    summary="Reconoce un evento de alerta",
)
async def ack_alert_event(event_id: int, request: Request) -> AlertEvent:
    """Marca el evento como reconocido: deja de notificar y anota el usuario."""
    actor = _actor(request)
    event = alerts.ack_event(event_id, actor)
    if event is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Evento de alerta no encontrado: {event_id}"
        )
    audit("alert.event.acked", actor=actor, target=f"event:{event_id}")
    return AlertEvent(**event)


@router.post(
    "/alert-events/{event_id}/snooze",
    response_model=AlertEvent,
    summary="Aplaza las notificaciones de un evento",
)
async def snooze_alert_event(
    event_id: int, payload: AlertSnoozeRequest, request: Request
) -> AlertEvent:
    """Silencia el evento durante los minutos indicados."""
    actor = _actor(request)
    event = alerts.snooze_event(event_id, payload.minutes)
    if event is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Evento de alerta no encontrado: {event_id}"
        )
    audit(
        "alert.event.snoozed",
        actor=actor,
        target=f"event:{event_id}",
        details={"minutos": payload.minutes},
    )
    return AlertEvent(**event)


# ======================= Canales de notificación =======================
@router.get(
    "/notification-channels",
    response_model=NotificationChannelListResponse,
    summary="Lista los canales de notificación",
)
async def list_notification_channels() -> NotificationChannelListResponse:
    """Devuelve los canales con la configuración enmascarada."""
    channels = notifications.list_channels()
    return NotificationChannelListResponse(
        count=len(channels),
        channels=[NotificationChannel(**channel) for channel in channels],
    )


@router.post(
    "/notification-channels",
    response_model=NotificationChannel,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un canal de notificación",
)
async def create_notification_channel(
    payload: NotificationChannelCreate, request: Request
) -> NotificationChannel:
    """Crea un canal tras validar su configuración (incluida la guarda SSRF)."""
    channel = notifications.create_channel(
        name=payload.name,
        kind=payload.kind,
        config=payload.config,
        enabled=payload.enabled,
    )
    audit(
        "alert.channel.created",
        actor=_actor(request),
        target=f"channel:{channel['id']}",
        details={"tipo": channel["kind"]},
    )
    return NotificationChannel(**channel)


@router.patch(
    "/notification-channels/{channel_id}",
    response_model=NotificationChannel,
    summary="Actualiza un canal de notificación",
)
async def update_notification_channel(
    channel_id: int, payload: NotificationChannelUpdate, request: Request
) -> NotificationChannel:
    """Actualiza los campos enviados del canal."""
    updated = notifications.update_channel(
        channel_id, payload.model_dump(exclude_unset=True)
    )
    if updated is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Canal no encontrado: {channel_id}"
        )
    audit("alert.channel.updated", actor=_actor(request), target=f"channel:{channel_id}")
    return NotificationChannel(**updated)


@router.delete(
    "/notification-channels/{channel_id}",
    response_model=ActionResponse,
    summary="Elimina un canal de notificación",
)
async def delete_notification_channel(channel_id: int, request: Request) -> ActionResponse:
    """Elimina el canal de notificación."""
    if not notifications.delete_channel(channel_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Canal no encontrado: {channel_id}"
        )
    audit("alert.channel.deleted", actor=_actor(request), target=f"channel:{channel_id}")
    return ActionResponse(success=True, message=f"Canal {channel_id} eliminado.")


@router.post(
    "/notification-channels/{channel_id}/test",
    response_model=ChannelTestResponse,
    summary="Envía un mensaje de prueba por un canal",
)
async def test_notification_channel(channel_id: int, request: Request) -> ChannelTestResponse:
    """Envía una carga de ejemplo para comprobar que el canal funciona."""
    if notifications.get_channel(channel_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Canal no encontrado: {channel_id}"
        )
    result = await notifications.send_test(channel_id)
    audit(
        "alert.channel.tested",
        actor=_actor(request),
        target=f"channel:{channel_id}",
        result="ok" if result["ok"] else "fail",
        details={"tipo": result.get("kind"), "detalle": result.get("detail")},
    )
    return ChannelTestResponse(**result)


@router.get(
    "/alerts/remediation",
    response_model=RemediationListResponse,
    summary="Propuestas de autorremediación pendientes",
)
async def list_remediation(
    limit: int = Query(default=20, ge=1, le=100, description="Máximo de propuestas.")
) -> RemediationListResponse:
    """Devuelve las propuestas de remediación que esperan confirmación.

    Cuando una regla con ``remediation_action`` abre un evento, el motor
    **propone** una acción (nunca la ejecuta). La persona la confirma desde el
    panel y solo entonces se aplica, por el mismo camino auditado que el resto
    de mutaciones.
    """
    items = remediation.open_proposals(limit=limit)
    return RemediationListResponse(
        count=len(items), proposals=[RemediationProposal(**item) for item in items]
    )


# ====================== Ventanas de mantenimiento ======================
@router.get(
    "/maintenance-windows",
    response_model=MaintenanceWindowListResponse,
    summary="Lista las ventanas de mantenimiento",
)
async def list_maintenance_windows() -> MaintenanceWindowListResponse:
    """Devuelve las ventanas configuradas."""
    windows = alerts.list_maintenance_windows()
    return MaintenanceWindowListResponse(
        count=len(windows), windows=[MaintenanceWindow(**item) for item in windows]
    )


@router.post(
    "/maintenance-windows",
    response_model=MaintenanceWindow,
    status_code=status.HTTP_201_CREATED,
    summary="Crea una ventana de mantenimiento",
)
async def create_maintenance_window(
    payload: MaintenanceWindowCreate, request: Request
) -> MaintenanceWindow:
    """Crea una ventana: durante ella los eventos se registran pero no avisan."""
    window = alerts.create_maintenance_window(payload.model_dump())
    audit(
        "alert.maintenance.created",
        actor=_actor(request),
        target=f"window:{window['id']}",
        details={"motivo": window["name"]},
    )
    return MaintenanceWindow(**window)


@router.patch(
    "/maintenance-windows/{window_id}",
    response_model=MaintenanceWindow,
    summary="Actualiza una ventana de mantenimiento",
)
async def update_maintenance_window(
    window_id: int, payload: MaintenanceWindowUpdate, request: Request
) -> MaintenanceWindow:
    """Actualiza los campos enviados de la ventana."""
    updated = alerts.update_maintenance_window(
        window_id, payload.model_dump(exclude_unset=True)
    )
    if updated is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Ventana no encontrada: {window_id}"
        )
    audit("alert.maintenance.updated", actor=_actor(request), target=f"window:{window_id}")
    return MaintenanceWindow(**updated)


@router.delete(
    "/maintenance-windows/{window_id}",
    response_model=ActionResponse,
    summary="Elimina una ventana de mantenimiento",
)
async def delete_maintenance_window(window_id: int, request: Request) -> ActionResponse:
    """Elimina la ventana de mantenimiento."""
    if not alerts.delete_maintenance_window(window_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Ventana no encontrada: {window_id}"
        )
    audit(
        "alert.maintenance.deleted", actor=_actor(request), target=f"window:{window_id}"
    )
    return ActionResponse(success=True, message=f"Ventana {window_id} eliminada.")


# ========================= Histórico de métricas =========================
@router.get(
    "/metrics-history",
    response_model=MetricsHistoryResponse,
    summary="Histórico de métricas (gráfica)",
)
async def metrics_history(
    range_key: RangeKey = Query(
        default="24h", alias="range", description="Ventana a representar."
    ),
    metric: str = Query(
        default="cpu,ram",
        description="Métricas separadas por comas (cpu,ram,swap,disk,load1,temp).",
    ),
    host_id: int = Query(
        default=metrics_store.LOCAL_HOST_ID,
        ge=1,
        description=(
            "Host de la flota consultable. Por defecto 1 (este servidor): sin este "
            "parámetro la gráfica es exactamente la de siempre."
        ),
    ),
) -> MetricsHistoryResponse:
    """Devuelve la serie downsampled desde el nivel de agregado adecuado.

    ``1h``/``24h`` → 1 m · ``7d`` → 5 m · ``30d`` → 1 h (máx. 1000 puntos).
    """
    return MetricsHistoryResponse(
        **metrics_store.history(range_key, _parse_metrics(metric), host_id=host_id)
    )


@router.get(
    "/metrics-export.csv",
    summary="Exporta el histórico de métricas en CSV",
    response_class=StreamingResponse,
)
async def metrics_export_csv(
    range_key: RangeKey = Query(
        default="24h", alias="range", description="Ventana a exportar."
    ),
) -> StreamingResponse:
    """Descarga el rango completo (sin remuestreo) como fichero CSV."""
    _period, header, rows = metrics_store.export_rows(range_key)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    buffer.seek(0)
    logger.info("Exportación CSV de métricas: rango={} filas={}", range_key, len(rows))
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="nexus-metricas-{range_key}.csv"'
        },
    )


# ============================== Auditoría ==============================
@router.get(
    "/audit",
    response_model=AuditListResponse,
    summary="Consulta el registro de auditoría",
)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=500, description="Entradas por página."),
    offset: int = Query(default=0, ge=0, description="Desplazamiento de la página."),
    action: str | None = Query(default=None, description="Filtra por acción exacta."),
    since: str | None = Query(
        default=None, description="Solo entradas con ts >= este valor (ISO8601)."
    ),
) -> AuditListResponse:
    """Lee la cadena de auditoría y devuelve una página, más reciente primero."""
    return AuditListResponse(
        **audit_log.read_events(limit=limit, offset=offset, action=action, since=since)
    )


__all__ = ["router"]