"""Agente de Nexus-Core para hosts remotos (fase multi-host).

El agente se instala en el servidor que se quiere vigilar y **conecta hacia
fuera** con el hub (HTTPS saliente), de modo que funciona detrás de NAT, sin
abrir puertos y sin reglas de firewall de entrada. Es el modelo clásico de
*pull*: el hub no puede iniciar una conexión hacia el agente, así que es el
agente quien mantiene la conexión abierta (long-poll) y quien empuja todo lo que
el panel necesita.

Ciclo de vida (una vuelta = un «tick»):

    heartbeat (métricas)  →  long-poll de comandos  →  ejecución local  →  resultado

Las métricas se leen con los **mismos módulos que el panel**
(:mod:`app.modules.system_monitor`), así que un host remoto informa exactamente
las mismas magnitudes que el host local; y las herramientas allowlisted
envuelven las mismas funciones que usa la API, de modo que el comportamiento no
se duplica ni se bifurga por mantener dos implementaciones.

Modos de uso::

    python agent.py enroll --hub https://nexus.example --enroll-token TOKEN --name "nodo-1"
    python agent.py run     --hub https://nexus.example
    python agent.py status                       # prueba de conectividad

Las credenciales se guardan en ``~/.config/nexus-agent/config.json`` con
permisos 0600 (o en la ruta que indique ``NEXUS_AGENT_CONFIG``). El bucle nunca
muere: cualquier error de red se reintenta con espera exponencial y cualquier
error de una herramienta se devuelve al hub como ``status='error'`` en lugar de
tumbar el agente.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import httpx

# Permite ejecutar el agente desde el propio repositorio (backend/agent.py) sin
# instalar el paquete: los módulos que reutiliza viven en ``app/``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.logging_conf import logger, setup_logging  # noqa: E402

#: Versión que se informa al hub en el enrolamiento y en cada latido.
AGENT_VERSION = "1.0.0"

#: Periodo del latido (segundos). Es la frecuencia de muestreo del panel.
HEARTBEAT_INTERVAL = 5.0

#: Espera del long-poll de comandos (segundos): corto para que la vuelta sea
#: parecida al latido y el tráfico idle siga siendo bajo (una conexión abierta).
POLL_WAIT = 4.0

#: Tope de la espera exponencial tras un fallo de red (segundos).
MAX_BACKOFF = 30.0

#: Nombre por defecto del fichero de configuración.
CONFIG_FILENAME = "config.json"

#: Variable de entorno que permite fijar otra ruta de configuración (tests,
#: instalaciones sin home utilizable, o varias instancias en una máquina).
CONFIG_ENV = "NEXUS_AGENT_CONFIG"


# ============================== Configuración =============================
def config_path() -> Path:
    """Ruta del fichero de configuración del agente."""
    override = os.environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / "nexus-agent" / CONFIG_FILENAME


def validate_hub_url(hub: str) -> str:
    """Exige HTTPS; solo permite HTTP loopback con un flag explícito de dev."""
    value = (hub or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("NEXUS_AGENT_ALLOW_HTTP", "") == "1"
    ):
        return value
    raise ValueError(
        "El hub del agente debe usar https://. Para pruebas locales en loopback, "
        "define NEXUS_AGENT_ALLOW_HTTP=1."
    )


def load_config() -> dict[str, Any]:
    """Lee la configuración guardada (error claro si el agente no está enrolado)."""
    path = config_path()
    if not path.exists():
        raise SystemExit(
            f"El agente no está enrolado: no existe {path}.\n"
            "Ejecuta primero:  python agent.py enroll --hub URL --enroll-token TOKEN --name NOMBRE"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"No se pudo leer la configuración {path}: {exc}") from exc
    if not data.get("hub") or not data.get("token"):
        raise SystemExit(f"La configuración {path} está incompleta (falta «hub» o «token»).")
    try:
        data["hub"] = validate_hub_url(str(data["hub"]))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return data


def save_config(data: dict[str, Any]) -> Path:
    """Guarda la configuración de forma atómica y con permisos 0600."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:  # pragma: no cover - en Windows los permisos POSIX no aplican
        pass
    os.replace(temporary, path)
    return path


# ============================== Identidad ================================
def identity(name: str = "") -> dict[str, str]:
    """Nombre, hostname y SO que se informan al hub."""
    return {
        "name": name or socket.gethostname(),
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}".strip(),
    }


def _json_safe(value: Any) -> Any:
    """Convierte un valor de retorno de un módulo en algo serializable en JSON.

    Los módulos del panel devuelven tuplas de ``namedtuple``, objetos de Docker o
    excepciones: el resultado viaja por HTTP, así que se normaliza aquí en lugar
    de obligar a cada módulo a hacerlo.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "isoformat"):  # datetime
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return {str(key): _json_safe(item) for key, item in vars(value).items()}
    return str(value)


def collect_metrics() -> dict[str, Any]:
    """Métricas del host con el mismo formato que guarda el panel."""
    from app.modules.metrics_normalize import normalize_sample as _normalize_sample
    from app.modules.system_monitor import get_system_metrics

    return _normalize_sample(get_system_metrics())


def collect_state() -> dict[str, Any]:
    """Instantánea ligera del host (contenedores y servicios detectados).

    Es best-effort: si algo no está disponible (sin Docker, sin systemd) se
    informa ``null`` en lugar de fallar el latido.
    """
    state: dict[str, Any] = {}
    try:
        from app.modules import docker_manager

        state["containers"] = len(docker_manager.list_containers())
    except Exception as exc:  # noqa: BLE001 - el inventario nunca rompe el latido
        logger.debug("Sin inventario de contenedores: {}", exc)
        state["containers"] = None
    try:
        from app.modules import systemd_manager

        state["services"] = len(systemd_manager.list_services().get("services") or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Sin inventario de servicios: {}", exc)
        state["services"] = None
    return state


# ========================= Herramientas allowlist =========================
def _docker() -> Any:
    """Importa el módulo de Docker bajo demanda (degrada si no está)."""
    from app.modules import docker_manager

    return docker_manager


def _services() -> Any:
    """Importa el inventario/controlled de servicios bajo demanda."""
    from app.modules import service_control, systemd_manager

    return service_control, systemd_manager


def _args(args: dict[str, Any], key: str, default: Any = None) -> Any:
    """Lee un argumento de la llamada con su valor por defecto."""
    value = args.get(key, default)
    return default if value is None else value


def _tool_metrics(args: dict[str, Any]) -> Any:
    """Métricas en el formato de la API del panel."""
    from app.modules.system_monitor import get_system_metrics

    return get_system_metrics()


def _tool_docker(args: dict[str, Any]) -> Any:
    """Subherramientas de Docker (list | logs | stats | deploy | remove | prune)."""
    manager = _docker()
    action = str(_args(args, "action", "list"))
    if action == "list":
        return manager.list_containers()
    if action == "logs":
        return manager.container_logs(
            str(_args(args, "container_id")), tail=int(_args(args, "tail", 200))
        )
    if action == "stats":
        return manager.container_stats(str(_args(args, "container_id")))
    if action == "deploy":
        return manager.deploy_container(
            str(_args(args, "image")),
            port=_args(args, "port"),
            name=_args(args, "name"),
        )
    if action == "remove":
        return manager.remove_container(
            str(_args(args, "container_id")), force=bool(_args(args, "force", True))
        )
    if action == "prune":
        # Igual que en el panel: la limpieza es irreversible y exige confirmación
        # explícita del administrador, que viaja dentro de los args.
        if not bool(_args(args, "confirm", False)):
            return {"success": False, "message": "Falta la confirmación «confirm: true»."}
        return manager.prune(
            images=bool(_args(args, "images", False)),
            volumes=bool(_args(args, "volumes", False)),
            containers=bool(_args(args, "containers", False)),
        )
    raise ValueError(f"Acción de Docker no soportada: {action}")


def _tool_services(args: dict[str, Any]) -> Any:
    """Subherramientas de servicios (list | detail | logs | action)."""
    control, systemd = _services()
    action = str(_args(args, "action", "list"))
    if action == "list":
        return systemd.list_services()
    if action == "detail":
        return systemd.get_service(str(_args(args, "name")))
    if action == "logs":
        return systemd.get_logs(str(_args(args, "name")), lines=int(_args(args, "lines", 100)))
    if action == "action":
        # ``actor`` es la etiqueta que quedará en la auditoría del host.
        return control.control(
            str(_args(args, "name")),
            str(_args(args, "service_action", "restart")),
            actor=str(_args(args, "actor", "nexus-agent")),
        )
    raise ValueError(f"Acción de servicio no soportada: {action}")


def _tool_network(args: dict[str, Any]) -> Any:
    """Subherramientas de red (interfaces | scan)."""
    from app.modules import network_scanner

    action = str(_args(args, "action", "interfaces"))
    if action == "interfaces":
        return _network_interfaces()
    if action == "scan":
        return network_scanner.scan_network(_args(args, "subnet"))
    raise ValueError(f"Acción de red no soportada: {action}")


def _network_interfaces() -> dict[str, Any]:
    """Interfaces de red con su estado (lectura de ``psutil``, sin root)."""
    import psutil

    addresses: dict[str, list[str]] = {}
    try:
        for name, entries in (psutil.net_if_addrs() or {}).items():
            addresses[name] = [entry.address for entry in entries if entry.address]
    except Exception as exc:  # noqa: BLE001 - degradación educada
        logger.debug("No se pudieron listar las direcciones de red: {}", exc)
    stats: dict[str, dict[str, Any]] = {}
    try:
        for name, counters in (psutil.net_if_stats() or {}).items():
            stats[name] = {
                "up": bool(counters.isup),
                "speed_mbps": counters.speed,
                "mtu": counters.mtu,
                "rx_bytes": counters.bytes_recv,
                "tx_bytes": counters.bytes_sent,
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("No se pudieron leer los contadores de red: {}", exc)
    # En algunos Windows psutil no expone net_if_stats (permisos): sin esto
    # el inventario saldría vacío aunque las direcciones sí se leyeron.
    for name in addresses:
        stats.setdefault(
            name,
            {"up": True, "speed_mbps": None, "mtu": None, "rx_bytes": None, "tx_bytes": None},
        )
    return {"interfaces": stats, "addresses": addresses}


def _tool_system(args: dict[str, Any]) -> Any:
    """Herramientas del sistema (ports | updates | smart)."""
    from app.modules import system_tools

    action = str(_args(args, "action", "ports"))
    if action == "ports":
        return system_tools.listening_ports()
    if action == "updates":
        return system_tools.apt_updates()
    if action == "smart":
        return system_tools.smart_disks()
    raise ValueError(f"Herramienta de sistema no soportada: {action}")


#: Helper privilegiado instalado por deploy/agent/install-agent.sh.
INSTALL_HELPER = "/usr/local/lib/nexus-agent/pkg-install"


def _tool_install(args: dict[str, Any]) -> Any:
    """Catálogo e instalación de servicios apt con allowlist.

    El agente no ejecuta apt directamente: llama a un helper root-owned que
    valida la clave contra /etc/nexus/installable.json. El agente solo puede
    pedir claves del catálogo; nunca nombres de paquete ni comandos.
    """
    if platform.system() != "Linux":
        return {
            "success": False,
            "available": False,
            "catalog": [],
            "message": "La instalación apt solo está disponible en hosts Linux.",
        }

    action = str(_args(args, "action", "catalog"))
    if action == "catalog":
        command = ["sudo", "-n", INSTALL_HELPER, "catalog"]
    elif action == "install":
        key = str(_args(args, "key", ""))
        if not key:
            raise ValueError("Falta la clave del servicio a instalar.")
        command = ["sudo", "-n", INSTALL_HELPER, "install", key]
    else:
        raise ValueError(f"Acción de instalación no soportada: {action}")

    result = subprocess.run(command, capture_output=True, text=True, timeout=170)
    output = (result.stdout or "").strip()
    if not output:
        raise RuntimeError(result.stderr.strip() or "El instalador no devolvió respuesta.")
    payload = json.loads(output)
    if result.returncode != 0 and isinstance(payload, dict):
        payload.setdefault("success", False)
    return payload


#: Herramientas que el hub puede ordenar. Es una **allowlist** explícita: el
#: hub nunca puede pedir código arbitrario, solo una de estas herramientas con
#: sus argumentos. ``diagnostics`` está declarada pero no implementada (el
#: paquete de diagnóstico es muy pesado para hacerlo bajo demanda); devuelve un
#: error estructurado en lugar de fingir que funciona.
TOOLS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "metrics": _tool_metrics,
    "docker": _tool_docker,
    "services": _tool_services,
    "network": _tool_network,
    "system": _tool_system,
    "install": _tool_install,
}


def supported_tools() -> list[str]:
    """Lista de herramientas (con sus subacciones) que admite el agente."""
    return [
        "metrics",
        "docker.list | docker.logs | docker.stats | docker.deploy | docker.remove | docker.prune",
        "services.list | services.detail | services.logs | services.action",
        "network.interfaces | network.scan",
        "system.ports | system.updates | system.smart",
        "install.catalog | install.install",
        "diagnostics? (no implementado: devuelve error)",
    ]


def execute(tool: str, args: dict[str, Any]) -> tuple[str, Any, str | None]:
    """Ejecuta una herramienta allowlisted y devuelve ``(estado, resultado, error)``.

    Nunca propaga excepciones: un fallo de una herramienta es un resultado
    ``error`` para el hub, y el bucle sigue vivo. Tampoco ejecuta nada fuera de
    la allowlist.
    """
    name, _, action = (tool or "").strip().partition(".")
    handler = TOOLS.get(name)
    if handler is None:
        if name == "diagnostics":
            return (
                "error",
                None,
                "El paquete de diagnóstico no está disponible bajo demanda en el agente.",
            )
        return (
            "error",
            None,
            f"Herramienta no permitida: '{tool}'. Disponibles: {', '.join(sorted(TOOLS))}.",
        )
    payload = dict(args or {})
    if action:
        payload["action"] = action
    try:
        return "done", _json_safe(handler(payload)), None
    except ValueError as exc:
        return "error", None, str(exc)
    except Exception as exc:  # noqa: BLE001 - ninguna herramienta tumba el bucle
        logger.warning("La herramienta {} falló: {}", tool, exc)
        return "error", None, f"{type(exc).__name__}: {exc}"


# ============================= Cliente del hub ===========================
class HubClient:
    """Cliente HTTP del agente hacia el hub (una conexión, salida siempre)."""

    def __init__(self, hub: str, token: str, timeout: float = 30.0) -> None:
        self.base = f"{validate_hub_url(hub)}/api/agent"
        self.headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Cierra el socket del cliente."""
        self._client.close()

    def __enter__(self) -> "HubClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def heartbeat(self, payload: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        """Envía las métricas del host y recibe el acuse."""
        response = self._client.post(
            f"{self.base}/heartbeat", json=payload, headers=self.headers, timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    def poll(self, wait: float) -> dict[str, Any] | None:
        """Long-poll de comandos. ``None`` si no hay nada (HTTP 204)."""
        response = self._client.get(
            f"{self.base}/commands",
            params={"wait": int(wait)},
            headers=self.headers,
            timeout=wait + 10.0,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def send_result(
        self, command_id: int, status: str, result: Any, error: str | None
    ) -> dict[str, Any]:
        """Devuelve al hub el resultado de un comando ejecutado."""
        response = self._client.post(
            f"{self.base}/commands/{command_id}/result",
            json={"status": status, "result": result, "error": error},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()


def enroll(hub: str, enroll_token: str, name: str) -> tuple[int, Path]:
    """Da de alta este host en el hub y guarda el token recibido."""
    try:
        hub = validate_hub_url(hub)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    who = identity(name)
    payload = {"token": enroll_token, "agent_version": AGENT_VERSION, **who}
    with httpx.Client(timeout=20.0) as client:
        response = client.post(f"{hub.rstrip('/')}/api/agent/enroll", json=payload)
    if response.status_code == 403:
        raise SystemExit(
            "El token de enrolamiento no es válido. Genéralo en el panel: "
            "GET /api/hosts/enrollment."
        )
    response.raise_for_status()
    body = response.json()
    path = save_config(
        {
            "hub": hub.rstrip("/"),
            "host_id": int(body["host_id"]),
            "token": str(body["token"]),
            "name": str(body.get("name") or who["name"]),
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent_version": AGENT_VERSION,
        }
    )
    return int(body["host_id"]), path


def run_loop(config: dict[str, Any], once: bool = False) -> int:
    """Bucle principal: latido → sondeo → ejecución → resultado.

    Los fallos de red se reintentan con espera exponencial (tope
    :data:`MAX_BACKOFF`): si el hub está apagado o se pierde la red a medio
    camino, el agente espera y reintenta en lugar de reiniciarse en bucle.
    """
    hub = str(config["hub"])
    token = str(config["token"])
    host_id = config.get("host_id")
    who = identity(str(config.get("name") or ""))
    logger.info(
        "Agente {} v{} hacia {} (host_id={})", AGENT_VERSION, AGENT_VERSION, hub, host_id
    )
    backoff = 0.0
    with HubClient(hub, token) as client:
        while True:
            started = time.monotonic()
            try:
                client.heartbeat(
                    {
                        "metrics": collect_metrics(),
                        "state": collect_state(),
                        "agent_version": AGENT_VERSION,
                        **who,
                    }
                )
                backoff = 0.0
            except (httpx.HTTPError, OSError) as exc:
                backoff = _wait_backoff(backoff, exc, "latido")
                if once:
                    return 1
                continue

            try:
                command = client.poll(POLL_WAIT)
            except (httpx.HTTPError, OSError) as exc:
                backoff = _wait_backoff(backoff, exc, "sondeo de comandos")
                if once:
                    return 1
                continue

            if command:
                _run_command(client, command)

            if once:
                return 0
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, HEARTBEAT_INTERVAL - elapsed))


def _run_command(client: HubClient, command: dict[str, Any]) -> None:
    """Ejecuta un comando recibido y devuelve el resultado (o el error)."""
    command_id = int(command.get("id", 0))
    tool = str(command.get("tool", ""))
    logger.info("Ejecutando {} (comando {})", tool, command_id)
    started = time.monotonic()
    status, result, error = execute(tool, dict(command.get("args") or {}))
    logger.info(
        "Comando {} -> {} en {:.2f} s", command_id, status, time.monotonic() - started
    )
    try:
        client.send_result(command_id, status, result, error)
    except (httpx.HTTPError, OSError) as exc:
        # El hub seará de cuenta con el tiempo de espera y marcará el comando
        # como expirado; no es motivo para tumbar el agente.
        logger.error("No se pudo devolver el resultado del comando {}: {}", command_id, exc)


def _wait_backoff(backoff: float, exc: BaseException, what: str) -> float:
    """Duplica la espera tras un fallo de red (tope ``MAX_BACKOFF``)."""
    delay = max(2.0, backoff * 2) if backoff else 2.0
    delay = min(delay, MAX_BACKOFF)
    logger.warning("Fallo en el {} ({}); reintento en {:.0f} s", what, exc, delay)
    time.sleep(delay)
    return delay


def status(config: dict[str, Any]) -> int:
    """Comprueba la conectividad con el hub (latido + inventario de métricas)."""
    who = identity(str(config.get("name") or ""))
    with HubClient(str(config["hub"]), str(config["token"])) as client:
        acuse = client.heartbeat(
            {
                "metrics": collect_metrics(),
                "agent_version": AGENT_VERSION,
                **who,
            }
        )
    print(f"Hub accesible. Host {acuse['host_id']} ({config.get('name')}), muestra {acuse['ts']}.")
    print("Herramientas admitidas:")
    for item in supported_tools():
        print(f"  - {item}")
    return 0


# ================================= CLI ==================================
def build_parser() -> argparse.ArgumentParser:
    """Constructor de argumentos de la CLI del agente."""
    parser = argparse.ArgumentParser(
        prog="nexus-agent",
        description="Agente de Nexus-Core para hosts remotos (conexión saliente al hub).",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    enrol = sub.add_parser("enroll", help="Da de alta este host en el hub.")
    enrol.add_argument("--hub", required=True, help="URL base del hub (https://nexus.example).")
    enrol.add_argument("--enroll-token", required=True, help="Token de enrolamiento del hub.")
    enrol.add_argument("--name", default="", help="Nombre del host en el panel.")

    runner = sub.add_parser("run", help="Bucle de latido y comandos (modo servicio).")
    runner.add_argument("--hub", default="", help="URL base del hub (si no, se usa la guardada).")
    runner.add_argument("--once", action="store_true", help="Una sola vuelta (diagnóstico).")

    probe = sub.add_parser("status", help="Prueba de conectividad y lista de herramientas.")
    probe.add_argument("--hub", default="", help="URL base del hub (si no, se usa la guardada).")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la línea de comandos."""
    setup_logging()
    args = build_parser().parse_args(argv)

    if args.mode == "enroll":
        host_id, path = enroll(args.hub, args.enroll_token, args.name)
        logger.info("Host {} enrolado. Credenciales en {}", host_id, path)
        print(f"Host {host_id} enrolado correctamente. Configuración: {path}")
        return 0

    config = load_config()
    if getattr(args, "hub", ""):
        config["hub"] = args.hub.rstrip("/")

    if args.mode == "status":
        return status(config)
    return run_loop(config, once=bool(getattr(args, "once", False)))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:  # pragma: no cover - parada manual
        logger.info("Agente detenido por el operador")
        raise SystemExit(0) from None