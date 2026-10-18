"""API de monitores de disponibilidad, estado público y exportador Prometheus.

Dos routers con permisos distintos:

* ``router`` (API **privada**, se registra con la dependencia ``require_auth``):
  CRUD de monitores, listado con estado/uptime ya calculados e histórico de
  heartbeats. Las acciones mutantes quedan anotadas en la cadena de auditoría.
* ``public_router`` (API **pública**, sin sesión ni CSRF): estado en JSON,
  página de estado en HTML y exportación a Prometheus. Son exactamente los tres
  puntos que un tercero puede consultar sin credenciales.

Decisiones de seguridad de la parte pública:

* ``/api/status/public`` **solo** expone el nombre elegido por el administrador,
  el tipo de comprobación, el estado, la latencia y los porcentajes de
  disponibilidad. Nunca devuelve ``target``, ``port``, ``keyword`` ni ``error``:
  el destino de un servicio interno no se publica por accidente (ver
  :func:`app.modules.monitors.public_status`).
* Ambos endpoints se sirven con ``Cache-Control`` corto (15 s): así un
  visorstatus externo no golpea la base de datos del panel cada few segundos
  pero los datos no envejecen tampoco.
* ``/api/metrics/prometheus`` es público por defecto, pero si se configura
  ``METRICS_TOKEN`` exige ``Authorization: Bearer <token>`` (401 si falta o no
  coincide). Es el punto de entrada para Prometheus en una red no confiable.
"""

from __future__ import annotations

import hmac
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.core.audit import audit
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.modules import monitors, prometheus
from app.schemas.models import (
    ActionResponse,
    Heartbeat,
    HeartbeatListResponse,
    Monitor,
    MonitorCreate,
    MonitorListResponse,
    MonitorUpdate,
    PublicStatusResponse,
)

#: Router privado del panel (exige sesión + CSRF, se registra en ``main.py``).
router = APIRouter(tags=["monitores"])

#: Router público (estado, página de estado y métricas Prometheus).
public_router = APIRouter(tags=["estado"])

#: Tiempo de caché de las respuestas públicas (segundos).
PUBLIC_CACHE_SECONDS = 15


def _actor(request: Request) -> str:
    """Usuario autenticado (la dependencia global del router ya lo validó)."""
    return str(request.state.session["username"])


def _require_monitor(monitor_id: int) -> dict[str, Any]:
    """Devuelve un monitor o lanza 404 si no existe."""
    monitor = monitors.get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Monitor no encontrado: {monitor_id}"
        )
    return monitor


def _as_monitor(monitor: dict[str, Any]) -> Monitor:
    """Convierte una fila de monitor (+derivados) en el modelo de la API."""
    return Monitor(**monitor)


# ============================== Monitores (privado) ==============================
@router.get(
    "/monitors",
    response_model=MonitorListResponse,
    summary="Lista los monitores de disponibilidad",
)
async def list_monitors() -> MonitorListResponse:
    """Devuelve todos los monitores con su estado y sus porcentajes de uptime."""
    summaries = monitors.list_summaries()
    return MonitorListResponse(
        count=len(summaries), monitors=[_as_monitor(item) for item in summaries]
    )


@router.post(
    "/monitors",
    response_model=Monitor,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un monitor de disponibilidad",
)
async def create_monitor(payload: MonitorCreate, request: Request) -> Monitor:
    """Crea un monitor (el destino se valida antes de tocar la base de datos)."""
    monitor = monitors.create_monitor(payload.model_dump())
    audit(
        "monitor.created",
        actor=_actor(request),
        target=f"monitor:{monitor['id']}",
        details={"tipo": monitor["kind"]},
    )
    return _as_monitor(monitors.monitor_summary(monitor))


@router.patch(
    "/monitors/{monitor_id}",
    response_model=Monitor,
    summary="Actualiza un monitor",
)
async def update_monitor(
    monitor_id: int, payload: MonitorUpdate, request: Request
) -> Monitor:
    """Actualiza los campos enviados (los ausentes se conservan)."""
    _require_monitor(monitor_id)
    updated = monitors.update_monitor(monitor_id, payload.model_dump(exclude_unset=True))
    if updated is None:  # pragma: no cover - borrado concurrente
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Monitor no encontrado: {monitor_id}"
        )
    audit("monitor.updated", actor=_actor(request), target=f"monitor:{monitor_id}")
    return _as_monitor(monitors.monitor_summary(updated))


@router.delete(
    "/monitors/{monitor_id}",
    response_model=ActionResponse,
    summary="Elimina un monitor",
)
async def delete_monitor(monitor_id: int, request: Request) -> ActionResponse:
    """Elimina el monitor y su histórico de heartbeats."""
    if not monitors.delete_monitor(monitor_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Monitor no encontrado: {monitor_id}"
        )
    audit("monitor.deleted", actor=_actor(request), target=f"monitor:{monitor_id}")
    return ActionResponse(success=True, message=f"Monitor {monitor_id} eliminado.")


@router.get(
    "/monitors/{monitor_id}/heartbeats",
    response_model=HeartbeatListResponse,
    summary="Histórico de comprobaciones de un monitor",
)
async def monitor_heartbeats(
    monitor_id: int,
    hours: int = Query(
        default=24, ge=1, le=720, description="Ventana a devolver, en horas (máx. 720)."
    ),
) -> HeartbeatListResponse:
    """Devuelve hasta 500 comprobaciones de la ventana, **la más reciente al final**."""
    _require_monitor(monitor_id)
    window = hours * 3600
    rows = monitors.list_heartbeats(monitor_id, since_ts=int(time.time()) - window)
    points = [
        Heartbeat(
            id=int(row["id"]),
            monitor_id=int(row["monitor_id"]),
            ts=int(row["ts"]),
            iso=monitors.iso_from_ts(int(row["ts"])),
            up=int(row["up"]),
            latency_ms=row["latency_ms"],
            status_code=row["status_code"],
            detail=row["detail"],
            error=row["error"],
        )
        for row in rows
    ]
    return HeartbeatListResponse(
        monitor_id=monitor_id, hours=hours, count=len(points), points=points
    )


# ============================== Estado público ==============================
@public_router.get(
    "/status/public",
    response_model=PublicStatusResponse,
    summary="Estado público del servicio (sin autenticación)",
)
async def public_status(response: Response) -> PublicStatusResponse:
    """Devuelve el estado global y el de cada monitor, sin datos del destino.

    Incluye **solo** el nombre dado por el administrador, el tipo de
    comprobación, el estado, la latencia y los porcentajes de 24 h y 7 días.
    """
    payload = monitors.public_status()
    response.headers["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"
    return PublicStatusResponse(**payload)


@public_router.get(
    "/status/page",
    response_class=HTMLResponse,
    summary="Página HTML de estado (sin autenticación)",
)
async def status_page() -> HTMLResponse:
    """Página de estado autocontenida (CSS y JS en línea, sin recursos externos).

    El HTML se sirve vacío de datos y el navegador pide
    ``/api/status/public``, que es la fuente de verdad única (mismos valores que
    cualquier otro consumidor). El nombre del monitor se pinta con
    ``textContent``, nunca con ``innerHTML``: un nombre con ``<`` no puede
    inyectar marcado en la página.
    """
    html = _STATUS_PAGE.replace("__API__", "/api/status/public")
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": f"public, max-age={PUBLIC_CACHE_SECONDS}"},
    )


@public_router.get(
    "/metrics/prometheus",
    response_class=PlainTextResponse,
    summary="Métricas en formato Prometheus (público o con token)",
)
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Devuelve las métricas del panel en formato de texto de Prometheus (0.0.4).

    Público por defecto. Si se define ``METRICS_TOKEN``, la petición debe
    incluir ``Authorization: Bearer <token>``; en caso contrario responde 401.
    """
    expected = get_settings().METRICS_TOKEN.strip()
    if expected:
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token or not hmac.compare_digest(
            token.strip(), expected
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Falta o es inválido el token de métricas (Authorization: Bearer).",
                headers={"WWW-Authenticate": "Bearer"},
            )
    body = prometheus.render()
    logger.debug("Exportación Prometheus: {} bytes", len(body))
    return PlainTextResponse(
        content=body,
        media_type=prometheus.CONTENT_TYPE,
        headers={"Cache-Control": f"public, max-age={PUBLIC_CACHE_SECONDS}"},
    )


# ============================ Página de estado ============================
# Plantilla mínima y autocontenida: sin CDN, sin fuentes externas, sin scripts
# inline de terceros (todo el CSS y el JS están embebidos aquí).
_STATUS_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estado del servicio</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0f1115; --panel: #171a21; --line: #262b36;
    --fg: #e7eaf0; --muted: #96a0b5; --ok: #3fb950; --warn: #d29922; --bad: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2.5rem 1rem; background: var(--bg); color: var(--fg);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  main { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  .sub { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }
  .banner {
    display: flex; align-items: center; gap: .6rem; padding: .9rem 1.1rem;
    border: 1px solid var(--line); border-radius: 10px; background: var(--panel);
    font-weight: 600;
  }
  .dot { width: 11px; height: 11px; border-radius: 50%; background: var(--muted); }
  table { width: 100%; border-collapse: collapse; margin-top: 1.5rem; }
  th, td { padding: .7rem .6rem; text-align: left; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: .8rem; font-weight: 600; }
  .up { background: rgba(63,185,80,.15); color: var(--ok); }
  .down { background: rgba(248,81,73,.15); color: var(--bad); }
  .unknown { background: rgba(214,153,34,.15); color: var(--warn); }
  .ok .dot { background: var(--ok); }
  .degraded .dot { background: var(--warn); }
  .down .dot { background: var(--bad); }
  footer { margin-top: 1.75rem; color: var(--muted); font-size: .78rem; }
  #error { color: var(--bad); }
</style>
</head>
<body>
<main>
  <h1>Estado del servicio</h1>
  <div class="sub">Comprobaciones automáticas de disponibilidad</div>
  <div class="banner" id="banner"><span class="dot"></span><span id="overall">Cargando…</span></div>
  <table>
    <thead>
      <tr>
        <th>Servicio</th><th>Tipo</th><th>Estado</th>
        <th class="num">Latencia</th><th class="num">24 h</th><th class="num">7 d</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <footer id="updated"></footer>
  <footer id="error"></footer>
</main>
<script>
const LABELS = { up: 'Operativo', down: 'Caído', unknown: 'Sin datos' };
const OVERALL = { up: 'Todos los servicios operativos', degraded: 'Servicio degradado', down: 'Servicio caído' };
const pct = (value) => (value === null || value === undefined ? '—' : value.toFixed(2) + ' %');

function cell(row, text, className) {
  const td = document.createElement('td');
  td.textContent = text;                       // textContent: nunca interpretamos HTML
  if (className) { td.className = className; }
  row.appendChild(td);
}

function badge(status) {
  const span = document.createElement('span');
  span.className = 'badge ' + status;
  span.textContent = LABELS[status] || status;
  return span;
}

function render(data) {
  const banner = document.getElementById('banner');
  banner.className = 'banner ' + data.overall;
  document.getElementById('overall').textContent = OVERALL[data.overall] || data.overall;

  const rows = document.getElementById('rows');
  rows.replaceChildren();
  for (const monitor of data.monitors) {
    const row = document.createElement('tr');
    cell(row, monitor.name);
    cell(row, monitor.kind);
    const state = document.createElement('td');
    state.appendChild(badge(monitor.status));
    row.appendChild(state);
    cell(row, monitor.latency_ms === null ? '—' : Math.round(monitor.latency_ms) + ' ms', 'num');
    cell(row, pct(monitor.uptime_24h), 'num');
    cell(row, pct(monitor.uptime_7d), 'num');
    rows.appendChild(row);
  }
  document.getElementById('updated').textContent = 'Actualizado: ' + data.updated_at;
}

fetch('__API__', { cache: 'no-store' })
  .then((response) => {
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    return response.json();
  })
  .then(render)
  .catch((err) => {
    document.getElementById('overall').textContent = 'No se pudo consultar el estado';
    document.getElementById('error').textContent = String(err);
  });
</script>
</body>
</html>
"""


__all__ = ["PUBLIC_CACHE_SECONDS", "public_router", "router"]