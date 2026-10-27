"""Colector de métricas en segundo plano (tarea asyncio del lifespan).

Un único bucle se encarga de tres trabajos, con intervalos independientes para
no desperdiciar CPU ni SD en una Raspberry Pi:

* **Muestreo** cada 5 s: recoge CPU, RAM, swap, disco, carga media y
  temperatura con :mod:`app.modules.system_monitor` (el mismo módulo que sirve
  ``GET /api/metrics``, así que ambas vistas coinciden), guarda la muestra
  —atribuida al host local, id 1— y entrega el valor al motor de alertas.
* **Agregados**: cada minuto reconstruye el último minuto cerrado (``1m``);
  cada hora reconstruye los últimos ``5m`` y ``1h`` a partir del nivel
  inferior. Al usar ``INSERT OR REPLACE``, recalcular es idempotente y
  autorreparable (llena huecos si el servicio se reinició).
* **Purga** cada hora según la retención de ``metrics_store``.

``psutil.cpu_percent(interval=0.5)`` bloquea el hilo medio segundo: por eso el
muestreo se ejecuta con ``asyncio.to_thread`` y nunca en el bucle de eventos.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from app.core import fleet
from app.core.db import LOCAL_HOST_ID
from app.core.logging_conf import logger
from app.modules import alerts, metrics_store, notifications
from app.modules.metrics_normalize import normalize_sample
from app.modules.system_monitor import get_system_metrics

#: Periodo de muestreo (segundos).
SAMPLE_INTERVAL = 5.0


# Alias histórico: el colector y los tests lo usaban con este nombre.
_normalize_sample = normalize_sample

#: Periodo de reconstrucción de agregados (segundos).
ROLLUP_INTERVAL = 60.0

#: Periodo de purga por retención (segundos).
PRUNE_INTERVAL = 3600.0

#: Cuántos aggregados cerrados se reconstruyen en cada pasada (el último y el
#: anterior): cubre un reinicio puntual del servicio sin multiplicar el trabajo.
ROLLUP_REBUILD = 2


class MetricsCollector:
    """Bucle de muestreo, agregados y purga de métricas.

    Se instancia una vez en el lifespan de la aplicación::

        collector = MetricsCollector()
        task = asyncio.create_task(collector.run())
        ...
        await collector.stop()

    ``sample_fn`` y ``notify`` son inyectables para poder probarlo sin tocar el
    sistema ni enviar notificaciones reales.
    """

    def __init__(
        self,
        sample_fn: Callable[[], dict[str, Any]] = get_system_metrics,
        notify: Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self._sample_fn = sample_fn
        self._notify = notify or notifications.dispatch_event
        self._stop = asyncio.Event()
        self._next_rollup = 0.0
        self._next_prune = 0.0

    # ------------------------------ API ------------------------------
    async def run(self) -> None:
        """Bucle principal: muestrea hasta que se le pide parar."""
        logger.info(
            "Colector de métricas iniciado (muestreo cada {} s)", SAMPLE_INTERVAL
        )
        # La primera agregación/purga se hace a los 60 s: así el arranque no
        # compete con el resto del lifespan.
        self._next_rollup = time.monotonic() + ROLLUP_INTERVAL
        self._next_prune = time.monotonic() + PRUNE_INTERVAL
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:  # pragma: no cover - parada explícita
                raise
            except Exception as exc:  # noqa: BLE001 - el bucle nunca debe morir
                logger.error("Fallo en el tick del colector de métricas: {}", exc)
            elapsed = time.monotonic() - started
            await self._sleep(max(0.5, SAMPLE_INTERVAL - elapsed))
        logger.info("Colector de métricas detenido")

    async def _sleep(self, seconds: float) -> None:
        """Espera interrumpible (permite parar sin esperar al siguiente tick)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def stop(self) -> None:
        """Pide la parada del bucle (idempotente)."""
        self._stop.set()

    # ---------------------------- Trabajo ----------------------------
    async def tick(self) -> None:
        """Ejecuta un ciclo: muestra, evalúa alertas y mantiene los agregados.

        Las muestras y las alertas se atribuyen al **host local** (id 1), que es
        esta máquina: en modo local no hay ningún otro host y el panel sigue
        viendo exactamente las mismas métricas de siempre. Los agentes remotos
        reportan por su cuenta contra el hub.
        """
        now = time.time()
        raw = await asyncio.to_thread(self._sample_fn)
        sample = _normalize_sample(raw)
        moment = int(now)
        try:
            metrics_store.insert_sample(moment, sample, LOCAL_HOST_ID)
        except Exception as exc:  # noqa: BLE001 - no perder el muestreo
            logger.error("No se pudo guardar la muestra de métricas: {}", exc)
        await self._notify_alerts(sample, now)
        await self._housekeeping(now)

    async def _notify_alerts(self, sample: dict[str, Any], now: float) -> None:
        """Evalúa las reglas y notifica las transiciones de alerta."""
        try:
            transitions = alerts.evaluate(sample, LOCAL_HOST_ID, now=now)
        except Exception as exc:  # noqa: BLE001 - la métrica es lo importante
            logger.error("Fallo evaluando las reglas de alerta: {}", exc)
            return
        for action, event, rule in transitions:
            try:
                await self._notify(action, event, rule)
            except Exception as exc:  # noqa: BLE001 - un canal no rompe el bucle
                logger.error("Fallo notificando la alerta {}: {}", action, exc)

    async def _housekeeping(self, now: float) -> None:
        """Reconstruye agregados cerrados y purga lo que caducó.

        El minuto (``1m``) se rehace en cada pasada de un minuto; los agregados
        de 5 minutos y de una hora, junto con la purga por retención, en la
        pasada horaria (así se ajusta el trabajo a una SD de Raspberry Pi).
        """
        moment = time.monotonic()
        if moment < self._next_rollup:
            self._mark_local_online(now)
            return
        self._next_rollup = moment + ROLLUP_INTERVAL
        hourly = moment >= self._next_prune
        self._rebuild_rollups(int(now), hourly=hourly)
        if not hourly:
            return
        self._next_prune = moment + PRUNE_INTERVAL
        try:
            metrics_store.prune(int(now))
        except Exception as exc:  # noqa: BLE001 - la purga no es crítica
            logger.error("Fallo en la purga de métricas: {}", exc)

    @staticmethod
    def _mark_local_online(now: float) -> None:
        """Refresca el «last seen» del host local en el inventario de la flota.

        Lo hace el propio colector (no un agente): así el host 1 aparece en
        línea mientras el servicio está arrancado y en línea solo, sin un
        heartbeat extra. La escritura se amortigua dentro de la ventana de
        conexión de :mod:`app.core.fleet`.
        """
        try:
            fleet.touch_local(now)
        except Exception as exc:  # noqa: BLE001 - el inventario no es crítico
            logger.debug("No se pudo refrescar el host local: {}", exc)

    def _rebuild_rollups(self, now: int, hourly: bool = False) -> None:
        """Recalcula los últimos buckets cerrados de cada periodo."""
        try:
            for index in range(ROLLUP_REBUILD):
                bucket = metrics_store.last_complete_bucket("1m", now) - index * 60
                metrics_store.build_rollup("1m", bucket)
            if not hourly:
                return
            for period in ("5m", "1h"):
                span = metrics_store.PERIOD_SECONDS[period]
                for index in range(ROLLUP_REBUILD):
                    bucket = metrics_store.last_complete_bucket(period, now) - index * span
                    metrics_store.build_rollup(period, bucket)
        except Exception as exc:  # noqa: BLE001 - los agregados no son críticos
            logger.error("Fallo reconstruyendo los agregados de métricas: {}", exc)


__all__ = ["PRUNE_INTERVAL", "ROLLUP_INTERVAL", "SAMPLE_INTERVAL", "MetricsCollector"]