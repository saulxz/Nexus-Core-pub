"""Comprobador de monitores en segundo plano (tarea asyncio del lifespan).

Un único bucle recorre los monitores habilitados cada ``TICK_INTERVAL`` segundos
y lanza la comprobación de los que tocan. El registro vive en memoria indexado
por ``id`` de monitor (``_due``), así que:

* un monitor recién creado (o reactivado) se comprueba **de inmediato**;
* borrarlo o desactivarlo lo saca del registro en la siguiente pasada, sin
  reiniciar el servicio (los cambios del panel no requieren ninguna señal);
* cada monitor se reprograma a ``interval_s`` tras su comprobación, de forma que
  cada uno va a su propio ritmo y una comprobación lenta no arrastra a los demás.

Cada comprobación deja un heartbeat (:mod:`app.modules.monitors`). **Ninguna
falla puede tumbar el bucle**: el ``try/except`` de ``run`` protege el tick
completo y ``_safe_check`` aísla cada monitor por separado.

Semántica de cada tipo (política SSRF documentada en :mod:`app.modules.monitors`):

``http``
    ``GET`` con el ``timeout_s`` del monitor y ``follow_redirects=False``. Está
    «arriba» si el código coincide con ``expected_status`` (si seindicó) o, en
    su defecto, si ``200 <= status < 400``. Si hay ``keyword``, el cuerpo debe
    contenerla (se leen como máximo 64 KB). Se guarda el código real y la
    latencia medida.
``tcp``
    ``asyncio.open_connection(host, port)`` con timeout. El puerto es
    obligatorio. La latencia es el tiempo real de conexión.
``ssl``
    Handshake TLS con ``ssl.create_default_context()`` (verificación completa:
    solo un certificado válido para ese host se considera disponible). El
    detalle guarda **los días que faltan para caducar** y el monitor está «arriba»
    si el handshake funciona y quedan más de 0 días.

Inyectabilidad: ``MonitorChecker(probes=...)`` sustituye las funciones de
comprobación, de modo que los tests pueden ejercitar el bucle completo sin
abrir ni un socket (y ``monitors`` + ``httpx`` se pueden monkeypatchear para
probar la lógica de un comprobante concreto). Además, ``MONITORS_ENABLED=false``
impide que arranque el bucle: los tests de la suite lo desactivan para no generar
peticiones salientes hacia los destinos que crean en sus datos.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from typing import Any, Awaitable, Callable, Mapping, NamedTuple

import httpx

from app.core.config import get_settings
from app.core.logging_conf import logger
from app.modules import monitors

#: Periodo del bucle: granularity con la que se lanzan las comprobaciones
#: pendientes (cada monitor sigue su propio ``interval_s``).
TICK_INTERVAL = 2.0

#: Cada cuánto se purgan los heartbeats que superan la retención.
PRUNE_INTERVAL = 3600.0

#: Espera mínima entre dos comprobaciones del mismo monitor (protege la SD si
#: alguien configura intervalos ridículos a mano en la base de datos).
MIN_INTERVAL_S = monitors.MIN_INTERVAL_S

#: Cortafuegos del texto de error guardado en el heartbeat.
MAX_ERROR_CHARS = monitors.MAX_ERROR_CHARS


class CheckResult(NamedTuple):
    """Resultado de una comprobación (lo que acaba en el heartbeat).

    Attributes:
        up: True si el destino está disponible según los criterios del monitor.
        latency_ms: tiempo real de la comprobación, o ``None`` si no se midió.
        status_code: código HTTP (solo monitores ``http``).
        detail: información legible del resultado (en ``ssl``, los días
            restantes de certificado).
        error: descripción del fallo; ``None`` si la comprobación pasó.
    """

    up: bool
    latency_ms: float | None = None
    status_code: int | None = None
    detail: str | None = None
    error: str | None = None


#: Función de comprobación de un tipo de monitor.
MonitorProbe = Callable[[Mapping[str, Any]], Awaitable[CheckResult]]


def _elapsed_ms(started: float) -> float:
    """Milisegundos transcurridos desde un instante de ``perf_counter``."""
    return round((time.perf_counter() - started) * 1000.0, 2)


def _error_text(exc: BaseException) -> str:
    """Describe una excepción como ``TipoDeError: mensaje``."""
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]


async def _read_limited(response: Any) -> str:
    """Lee como máximo ``MAX_BODY_BYTES`` del cuerpo de una respuesta.

    Solo se usa cuando el monitor define una palabra clave: acotar la lectura
    evita que un destino giganticamente grande agote la memoria de la Pi.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        received += len(chunk)
        if received >= monitors.MAX_BODY_BYTES:
            break
    return b"".join(chunks)[: monitors.MAX_BODY_BYTES].decode("utf-8", errors="replace")


# ============================== Comprobantes ==============================
async def probe_http(monitor: Mapping[str, Any]) -> CheckResult:
    """Comprueba un monitor ``http`` con un único ``GET`` sin redirecciones."""
    target = monitors.parse_target(
        "http", str(monitor["target"]), monitor.get("port")
    )
    timeout = float(monitor["timeout_s"])
    expected = monitor.get("expected_status")
    keyword = str(monitor.get("keyword") or "")
    started = time.perf_counter()
    status_code: int | None = None
    body = ""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", target.url) as response:
                status_code = int(response.status_code)
                if keyword:
                    body = await _read_limited(response)
    except Exception as exc:  # noqa: BLE001 - un fallo es un resultado, no un error
        return CheckResult(False, _elapsed_ms(started), status_code, None, _error_text(exc))

    latency = _elapsed_ms(started)
    up = status_code == int(expected) if expected is not None else 200 <= status_code < 400
    detail = f"HTTP {status_code}"
    if up and keyword and keyword not in body:
        up = False
        detail = f"{detail}; no se encontró la palabra clave"
    return CheckResult(up, latency, status_code, detail, None)


async def probe_tcp(monitor: Mapping[str, Any]) -> CheckResult:
    """Comprueba que un monitor ``tcp`` acepta conexiones en el puerto."""
    target = monitors.parse_target("tcp", str(monitor["target"]), monitor.get("port"))
    timeout = float(monitor["timeout_s"])
    writer: Any = None
    started = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=timeout
        )
        return CheckResult(True, _elapsed_ms(started), None, target.url, None)
    except Exception as exc:  # noqa: BLE001 - ver probe_http
        return CheckResult(False, _elapsed_ms(started), None, None, _error_text(exc))
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - cerrar no debe ensuciar el resultado
                pass


async def probe_ssl(monitor: Mapping[str, Any]) -> CheckResult:
    """Comprueba el handshake TLS y los días que faltan para caducar el certificado."""
    target = monitors.parse_target("ssl", str(monitor["target"]), monitor.get("port"))
    timeout = float(monitor["timeout_s"])
    writer: Any = None
    started = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                target.host,
                target.port,
                ssl=ssl.create_default_context(),
                server_hostname=target.host,
            ),
            timeout=timeout,
        )
        latency = _elapsed_ms(started)
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert() if ssl_object is not None else None
        not_after = (certificate or {}).get("notAfter")
        if not not_after:
            # Handshake correcto pero sin datos de validez: no se puede afirmar
            # que quede margen, así que se marca caído en lugar de inventar.
            return CheckResult(
                False, latency, None, None, "El certificado no expone su fecha de caducidad."
            )
        days_left = int((ssl.cert_time_to_seconds(not_after) - time.time()) // 86400)
        return CheckResult(
            days_left > 0,
            latency,
            None,
            str(days_left),
            None if days_left > 0 else "El certificado ha caducado o está a punto de caducar.",
        )
    except Exception as exc:  # noqa: BLE001 - ver probe_http
        return CheckResult(False, _elapsed_ms(started), None, None, _error_text(exc))
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 - ver probe_tcp
                pass


#: Comprobantes por tipo de monitor (se pueden sustituir en los tests).
PROBES: dict[str, MonitorProbe] = {
    "http": probe_http,
    "tcp": probe_tcp,
    "ssl": probe_ssl,
}


# ================================ Bucle ================================
class MonitorChecker:
    """Comprueba periódicamente los monitores habilitados y guarda heartbeats.

    Se instancia una vez en el lifespan de la aplicación::

        checker = MonitorChecker()
        task = asyncio.create_task(checker.run())
        ...
        await checker.stop()

    ``probes`` es inyectable: un ``{tipo: función}`` en lugar del registro real
    permite probar el bucle completo sin red (y sin ``httpx``).
    """

    def __init__(self, probes: Mapping[str, MonitorProbe] | None = None) -> None:
        self._probes = dict(probes) if probes is not None else None
        self._due: dict[int, float] = {}
        self._stop = asyncio.Event()
        self._next_prune = 0.0

    # ------------------------------ API ------------------------------
    async def run(self) -> None:
        """Bucle principal: comprueba los monitores vencidos hasta que se pare."""
        if not get_settings().MONITORS_ENABLED:
            logger.info("Comprobador de monitores desactivado por configuración")
            return
        logger.info(
            "Comprobador de monitores iniciado (revisión cada {} s)", TICK_INTERVAL
        )
        self._next_prune = time.monotonic() + PRUNE_INTERVAL
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:  # pragma: no cover - parada explícita
                raise
            except Exception as exc:  # noqa: BLE001 - el bucle nunca debe morir
                logger.error("Fallo en el tick del comprobador de monitores: {}", exc)
            elapsed = time.monotonic() - started
            await self._sleep(max(0.1, TICK_INTERVAL - elapsed))
        logger.info("Comprobador de monitores detenido")

    async def stop(self) -> None:
        """Pide la parada del bucle (idempotente)."""
        self._stop.set()

    async def tick(self) -> None:
        """Recarga el registro y comprueba los monitores que tocan."""
        monitors_due = self._reload()
        if monitors_due:
            await asyncio.gather(*(self._safe_check(item) for item in monitors_due))
        self._housekeeping()

    async def check(self, monitor: Mapping[str, Any]) -> CheckResult:
        """Comprueba un monitor y guarda su heartbeat (uso directo en tests)."""
        return await self._run_probe(monitor)

    # ---------------------------- Trabajo ----------------------------
    def _probe_for(self, kind: str) -> MonitorProbe:
        """Devuelve el comprobante de un tipo de monitor."""
        probes = self._probes if self._probes is not None else PROBES
        probe = probes.get(kind)
        if probe is None:
            raise KeyError(f"Tipo de monitor no soportado: '{kind}'.")
        return probe

    async def _run_probe(self, monitor: Mapping[str, Any]) -> CheckResult:
        """Ejecuta el comprobante del monitor, validando antes su destino."""
        try:
            parsed = monitors.parse_target(
                str(monitor["kind"]), str(monitor["target"]), monitor.get("port")
            )
        except monitors.TargetError as exc:
            return CheckResult(False, None, None, None, str(exc))
        probe = self._probe_for(str(monitor["kind"]))
        try:
            return await probe({**monitor, "target": parsed.url})
        except Exception as exc:  # noqa: BLE001 - un comprobante no puede tumbar el bucle
            logger.error(
                "El comprobante de '{}' falló: {}", monitor.get("name"), _error_text(exc)
            )
            return CheckResult(False, None, None, None, _error_text(exc))

    async def _safe_check(self, monitor: Mapping[str, Any]) -> None:
        """Comprueba un monitor y registra su heartbeat, aislando cualquier fallo."""
        monitor_id = int(monitor["id"])
        try:
            result = await self._run_probe(monitor)
            monitors.insert_heartbeat(
                monitor_id,
                int(time.time()),
                result.up,
                result.latency_ms,
                result.status_code,
                result.detail,
                result.error,
            )
        except Exception as exc:  # noqa: BLE001 - un monitor no puede parar el bucle
            logger.error(
                "No se pudo comprobar el monitor {} ({}): {}",
                monitor_id,
                monitor.get("name"),
                _error_text(exc),
            )
            return
        if not result.up:
            logger.warning(
                "Monitor {} ('{}') caído: {}",
                monitor_id,
                monitor.get("name"),
                result.error or result.detail or "sin detalle",
            )

    def _reload(self) -> list[dict[str, Any]]:
        """Relee los monitores habilitados y devuelve los que deben comprobarse.

        Es lo que hace que los cambios del panel (alta, edición, borrado,
        activar/desactivar) se apliquen sin reiniciar el servicio.
        """
        active = monitors.list_monitors(enabled_only=True)
        ids = {int(item["id"]) for item in active}
        for gone in set(self._due) - ids:
            self._due.pop(gone, None)

        now = time.monotonic()
        due: list[dict[str, Any]] = []
        for item in active:
            monitor_id = int(item["id"])
            if monitor_id not in self._due:
                # Monitor nuevo o reactivado: se comprueba en la siguiente pasada.
                self._due[monitor_id] = now
            if now < self._due[monitor_id]:
                continue
            interval = max(MIN_INTERVAL_S, int(item["interval_s"]))
            self._due[monitor_id] = now + interval
            due.append(item)
        return due

    def _housekeeping(self) -> None:
        """Purga los heartbeats caducados una vez por hora."""
        now = time.monotonic()
        if now < self._next_prune:
            return
        self._next_prune = now + PRUNE_INTERVAL
        try:
            monitors.prune_heartbeats(int(time.time()) - monitors.HEARTBEAT_RETENTION_S)
        except Exception as exc:  # noqa: BLE001 - la purga no es crítica
            logger.error("Fallo en la purga de heartbeats: {}", exc)

    async def _sleep(self, seconds: float) -> None:
        """Espera interrumpible (permite parar sin esperar al siguiente tick)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass


__all__ = [
    "PROBES",
    "PRUNE_INTERVAL",
    "TICK_INTERVAL",
    "CheckResult",
    "MonitorChecker",
    "MonitorProbe",
    "probe_http",
    "probe_ssl",
    "probe_tcp",
]