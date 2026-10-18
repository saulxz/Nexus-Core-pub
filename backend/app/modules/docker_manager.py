"""Gestión de contenedores Docker mediante el SDK oficial de Python.

Este módulo encapsula toda la interacción con el daemon de Docker a través del
socket montado en ``/var/run/docker.sock``. Expone operaciones de alto nivel
(listar, desplegar y destruir) y aplica medidas de seguridad:

* **Whitelist de imágenes** (``ALLOWED_IMAGES``) para impedir desplegar
  imágenes arbitrarias.
* **Límite de contenedores** en ejecución (``MAX_RUNNING_CONTAINERS``).
* **Etiquetado** de los contenedores gestionados por Nexus-Core.
* Ninguna función propaga excepciones: todas devuelven diccionarios
  estructurados con el resultado de la operación.
"""

from __future__ import annotations

import random
import string
import time
from typing import Any

import docker
import psutil
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app.core.config import get_settings
from app.core.logging_conf import logger

# Cliente Docker perezoso (singleton). Se crea en la primera llamada para no
# bloquear el arranque de la aplicación si el socket no está disponible.
_client: "docker.DockerClient | None" = None

# Última lectura de contadores de CPU por contenedor, para calcular el delta del
# endpoint /containers/{id}/stats (Docker no expone porcentajes).
_CPU_SAMPLES: dict[str, tuple[int, int]] = {}

# Suprime el spam de errores: si el socket no es accesible, solo se registra
# un aviso por minuto (el panel sondea cada pocos segundos).
_ERROR_LOG_INTERVAL_SECONDS = 60.0
_last_error_log = 0.0

# Puerto interno por defecto de cada imagen, usado al publicar puertos.
_IMAGE_PORT_MAP: dict[str, int] = {
    "alpine": 80,
    "busybox": 80,
    "nginx": 80,
    "httpd": 80,
    "traefik": 80,
    "redis": 6379,
    "postgres": 5432,
    "mysql": 3306,
    "mariadb": 3306,
    "portainer/portainer-ce": 9000,
    "portainer/portainer": 9000,
}


def _get_client() -> "docker.DockerClient":
    """Crea (una sola vez) y devuelve el cliente Docker.

    Raises:
        DockerException: si no se puede contactar con el daemon.
    """
    global _client
    if _client is None:
        settings = get_settings()
        logger.info("Conectando con el daemon de Docker en {}", settings.DOCKER_SOCKET)
        _client = docker.DockerClient(base_url=settings.DOCKER_SOCKET, timeout=15)
    return _client


def _random_suffix(length: int = 6) -> str:
    """Genera un sufijo aleatorio para nombres de contenedor únicos."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def _image_name(container: Any) -> str:
    """Extrae el nombre legible de la imagen de un contenedor."""
    try:
        tags = container.image.tags
        if tags:
            return tags[0]
        return container.image.short_id
    except Exception:  # noqa: BLE001 - la imagen puede haberse eliminado
        return "desconocida"


def _default_container_port(image: str) -> int:
    """Devuelve el puerto interno por defecto asociado a una imagen."""
    base = image.split(":")[0].strip().lower()
    if base in _IMAGE_PORT_MAP:
        return _IMAGE_PORT_MAP[base]
    # Si la imagen usa un namespace (p. ej. "usuario/imagen"), comprueba el sufijo.
    if "/" in base and base in _IMAGE_PORT_MAP:
        return _IMAGE_PORT_MAP[base]
    return 80


def _serialize_container(container: Any) -> dict[str, Any]:
    """Convierte un objeto contenedor del SDK en un diccionario serializable."""
    attrs: dict[str, Any] = container.attrs or {}
    name = (container.name or "").lstrip("/")

    # Construye la lista legible de mapeos de puertos publicados.
    ports: list[str] = []
    port_map = (attrs.get("NetworkSettings", {}) or {}).get("Ports", {}) or {}
    for container_port, bindings in port_map.items():
        if bindings:
            for binding in bindings:
                host_ip = binding.get("HostIp", "0.0.0.0")
                host_port = binding.get("HostPort", "")
                ports.append(f"{host_ip}:{host_port} -> {container_port}")
        else:
            ports.append(container_port)

    return {
        "id": container.id,
        "short_id": container.id[:12],
        "name": name,
        "image": _image_name(container),
        "status": container.status,
        "state": container.status,
        "ports": ports,
        "created": attrs.get("Created"),
    }


def list_containers() -> list[dict[str, Any]]:
    """Lista los contenedores del host.

    Returns:
        Lista de diccionarios con id, nombre, imagen, estado y puertos. Si el
        daemon no está disponible, devuelve una lista vacía y registra el error.
    """
    try:
        client = _get_client()
        containers = client.containers.list(all=True)
        return [_serialize_container(c) for c in containers]
    except (DockerException, APIError) as exc:
        global _last_error_log
        now = time.time()
        if now - _last_error_log >= _ERROR_LOG_INTERVAL_SECONDS:
            logger.error("No se pudieron listar los contenedores: {}", exc)
            _last_error_log = now
        return []


def get_container(container_id: str) -> dict[str, Any] | None:
    """Obtiene la información de un contenedor concreto por su ID o nombre."""
    try:
        client = _get_client()
        return _serialize_container(client.containers.get(container_id))
    except NotFound:
        logger.warning("Contenedor no encontrado: {}", container_id)
        return None
    except (DockerException, APIError) as exc:
        logger.error("Error obteniendo el contenedor {}: {}", container_id, exc)
        return None


def _count_managed_running() -> int:
    """Cuenta los contenedores gestionados por Nexus-Core que están en marcha."""
    settings = get_settings()
    try:
        client = _get_client()
        running = client.containers.list(
            filters={"status": "running", "label": "managed_by=nexus-core"}
        )
        # Se cuentan también los que tengan el prefijo por si perdieron etiqueta.
        prefixed = [c for c in running if (c.name or "").lstrip("/").startswith(settings.CONTAINER_NAME_PREFIX)]
        return max(len(running), len(prefixed))
    except (DockerException, APIError):
        return 0


def deploy_container(
    image: str,
    port: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Despliega un contenedor de forma controlada.

    Args:
        image: Nombre de la imagen (debe pertenecer a la whitelist).
        port: Puerto del host a publicar. Si es ``None``, el contenedor se
            ejecuta sin puertos publicados.
        name: Nombre opcional (se le aplica el prefijo de Nexus-Core).

    Returns:
        Diccionario con ``success``, ``message`` y ``container`` (si se creó).
    """
    settings = get_settings()
    image = (image or "").strip().lower()

    # --- Validación de seguridad: whitelist de imágenes ---
    if image not in settings.allowed_images_list:
        message = (
            f"Imagen no permitida: '{image}'. "
            f"Imágenes autorizadas: {', '.join(settings.allowed_images_list)}."
        )
        logger.warning(message)
        return {"success": False, "message": message, "container": None}

    # --- Validación de seguridad: límite de contenedores gestionados ---
    running_count = _count_managed_running()
    if running_count >= settings.MAX_RUNNING_CONTAINERS:
        message = (
            f"Se alcanzó el límite de {settings.MAX_RUNNING_CONTAINERS} "
            "contenedores gestionados en ejecución."
        )
        logger.warning(message)
        return {"success": False, "message": message, "container": None}

    try:
        client = _get_client()

        # Nombre final: prefijo de la plataforma + sufijo aleatorio si no se da.
        safe_base = image.split(":")[0].split("/")[-1]
        final_name = name.strip() if name else f"{settings.CONTAINER_NAME_PREFIX}{safe_base}-{_random_suffix()}"
        if not final_name.startswith(settings.CONTAINER_NAME_PREFIX):
            final_name = f"{settings.CONTAINER_NAME_PREFIX}{final_name}"

        # Mapeo dinámico de puertos: puerto del host -> puerto interno de la imagen.
        ports: dict[str, int] | None = None
        if port is not None:
            container_port = _default_container_port(image)
            ports = {f"{container_port}/tcp": int(port)}

        container = client.containers.run(
            image=image,
            name=final_name,
            detach=True,
            ports=ports,
            labels={"managed_by": "nexus-core", "nexus.image": image},
            restart_policy={"Name": "unless-stopped"},
        )

        info = _serialize_container(container)
        message = f"Contenedor '{final_name}' desplegado correctamente desde '{image}'."
        logger.info(message)
        return {"success": True, "message": message, "container": info}

    except ImageNotFound:
        message = f"La imagen '{image}' no está disponible localmente y no se pudo descargar."
        logger.error(message)
        return {"success": False, "message": message, "container": None}
    except (APIError, DockerException, ValueError) as exc:
        message = f"Error al desplegar el contenedor: {exc}"
        logger.error(message)
        return {"success": False, "message": message, "container": None}


def remove_container(container_id: str, force: bool = True) -> dict[str, Any]:
    """Destruye un contenedor por su ID o nombre.

    Args:
        container_id: Identificador completo, corto o nombre del contenedor.
        force: Si es ``True``, lo detiene y elimina forzosamente.

    Returns:
        Diccionario con ``success`` y ``message``.
    """
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        name = (container.name or container_id).lstrip("/")
        container.remove(force=force)
        message = f"Contenedor '{name}' eliminado correctamente."
        logger.info(message)
        return {"success": True, "message": message}
    except NotFound:
        message = f"Contenedor no encontrado: '{container_id}'."
        logger.warning(message)
        return {"success": False, "message": message}
    except (APIError, DockerException) as exc:
        message = f"Error al eliminar el contenedor '{container_id}': {exc}"
        logger.error(message)
        return {"success": False, "message": message}


def restart_container(container_id: str) -> dict[str, Any]:
    """Reinicia un contenedor por su ID o nombre.

    Es la acción que propone la autorremediación cuando una alerta de
    «contenedor caído» se dispara. Como toda mutación, solo llega aquí tras la
    confirmación humana del panel.

    Returns:
        ``{"success", "message", "container"}``.
    """
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        name = (container.name or container_id).lstrip("/")
        container.restart(timeout=20)
        logger.info("Contenedor '{}' reiniciado.", name)
        return {
            "success": True,
            "message": f"Contenedor '{name}' reiniciado correctamente.",
            "container": _serialize_container(client.containers.get(container_id)),
        }
    except NotFound:
        message = f"Contenedor no encontrado: '{container_id}'."
        logger.warning(message)
        return {"success": False, "message": message, "container": None}
    except (APIError, DockerException) as exc:
        message = f"Error al reiniciar el contenedor '{container_id}': {exc}"
        logger.error(message)
        return {"success": False, "message": message, "container": None}


# ====================== Inventario extendido de Docker ==================
def _unavailable(reason: str) -> dict[str, Any]:
    """Respuesta uniforme cuando el daemon de Docker no responde."""
    return {"available": False, "reason": reason, "items": []}


def list_volumes() -> dict[str, Any]:
    """Lista los volúmenes Docker (nombre, driver, punto de montaje)."""
    try:
        client = _get_client()
        items = []
        for volume in client.volumes.list():
            attrs = volume.attrs or {}
            items.append(
                {
                    "name": volume.name,
                    "driver": attrs.get("Driver", ""),
                    "mountpoint": attrs.get("Mountpoint", ""),
                    "scope": attrs.get("Scope", ""),
                }
            )
        return {"available": True, "reason": None, "items": items}
    except (DockerException, APIError) as exc:
        return _unavailable(f"No se pudieron listar los volúmenes: {exc}")


def list_networks() -> dict[str, Any]:
    """Lista las redes Docker (con sus contenedores conectados)."""
    try:
        client = _get_client()
        items = []
        for network in client.networks.list():
            attrs = network.attrs or {}
            items.append(
                {
                    "name": network.name,
                    "driver": attrs.get("Driver", ""),
                    "scope": attrs.get("Scope", ""),
                    "internal": bool(attrs.get("Internal", False)),
                    "containers": [
                        {
                            "name": data.get("Name", ""),
                            "ipv4": (data.get("IPv4Address") or "").split("/")[0],
                        }
                        for data in (attrs.get("Containers") or {}).values()
                        if isinstance(data, dict)
                    ],
                }
            )
        return {"available": True, "reason": None, "items": items}
    except (DockerException, APIError) as exc:
        return _unavailable(f"No se pudieron listar las redes: {exc}")


def list_images() -> dict[str, Any]:
    """Lista las imágenes locales con sus tags y tamaño."""
    try:
        client = _get_client()
        items = []
        for image in client.images.list(all=False):
            attrs = image.attrs or {}
            tags = list(image.tags) if image.tags else ["<sin tag>"]
            items.append(
                {
                    "id": image.id,
                    "short_id": (image.short_id or "")[:12],
                    "tags": tags,
                    "size_bytes": int(attrs.get("Size") or 0),
                    "created": attrs.get("Created"),
                }
            )
        return {"available": True, "reason": None, "items": items}
    except (DockerException, APIError) as exc:
        return _unavailable(f"No se pudieron listar las imágenes: {exc}")


def container_logs(container_id: str, tail: int = 200) -> dict[str, Any]:
    """Últimas líneas del log de un contenedor.

    Args:
        container_id: ID o nombre del contenedor.
        tail: Número de líneas (se acota a 1..5000).
    """
    lines = max(1, min(int(tail), 5000))
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        raw = container.logs(tail=lines, timestamps=True)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        collected = text.splitlines()
        return {
            "success": True,
            "container_id": container_id,
            "tail": lines,
            "count": len(collected),
            "lines": collected,
        }
    except NotFound:
        return {
            "success": False,
            "container_id": container_id,
            "tail": lines,
            "count": 0,
            "lines": [],
            "message": f"Contenedor no encontrado: '{container_id}'.",
        }
    except (DockerException, APIError) as exc:
        return {
            "success": False,
            "container_id": container_id,
            "tail": lines,
            "count": 0,
            "lines": [],
            "message": f"Error leyendo el log de '{container_id}': {exc}",
        }


def container_stats(container_id: str) -> dict[str, Any]:
    """Muestra puntual del consumo de CPU y memoria de un contenedor.

    Usa ``stream=False``: una sola lectura (el panel la consulta bajo demanda,
    no mantiene una suscripción permanente al stream de Docker).
    """
    try:
        client = _get_client()
        container = client.containers.get(container_id)
        raw = container.stats(stream=False) or {}
        memory = raw.get("memory_stats") or {}
        usage = memory.get("usage")
        limit = memory.get("limit")
        return {
            "success": True,
            "container_id": container_id,
            "stats": {
                "cpu_percent": _cpu_percent(container_id, raw.get("cpu_stats") or {}),
                "memory_usage_bytes": int(usage or 0),
                "memory_limit_bytes": int(limit or 0),
                "memory_percent": round(100.0 * int(usage or 0) / int(limit), 1)
                if limit
                else None,
                "net_io": raw.get("networks") or {},
                "block_read_bytes": (raw.get("blkio_stats") or {})
                .get("io_service_bytes_recursive", [{}])[0]
                .get("value", 0),
            },
        }
    except NotFound:
        return {
            "success": False,
            "container_id": container_id,
            "stats": {},
            "message": f"Contenedor no encontrado: '{container_id}'.",
        }
    except (DockerException, APIError, IndexError, TypeError) as exc:
        return {
            "success": False,
            "container_id": container_id,
            "stats": {},
            "message": f"Error obteniendo estadísticas de '{container_id}': {exc}",
        }


def _cpu_percent(container_id: str, cpu_stats: dict[str, Any]) -> float | None:
    """Uso de CPU en % comparando con la lectura anterior del contenedor.

    Docker solo entrega contadores acumulados (``total_usage`` de ticks de CPU y
    ``system_cpu_usage`` del host): el porcentaje sale del delta entre dos
    lecturas, así que se guarda la anterior en memoria. La primera consulta de
    un contenedor devuelve ``None`` (aún no hay con qué comparar); a partir de
    la segunda, si el panel sondea cada pocos segundos, el valor es correcto.
    """
    usage = cpu_stats.get("cpu_usage") or {}
    system = cpu_stats.get("system_cpu_usage") or 0
    total = int(usage.get("total_usage") or 0)
    if not total or not system:
        _CPU_SAMPLES.pop(container_id, None)
        return None
    previous = _CPU_SAMPLES.get(container_id)
    _CPU_SAMPLES[container_id] = (total, int(system))
    if previous is None:
        return None
    cpu_delta = total - previous[0]
    system_delta = int(system) - previous[1]
    if cpu_delta <= 0 or system_delta <= 0:
        return None
    cores = cpu_stats.get("online_cpus") or psutil.cpu_count(logical=True) or 1
    return round(min(100.0, 100.0 * cpu_delta / system_delta * int(cores)), 1)


def prune(images: bool = False, volumes: bool = False, containers: bool = False) -> dict[str, Any]:
    """Limpia recursos Docker sin uso (imágenes, volúmenes y/o contenedores).

    Es **destructivo e irreversible**: el endpoint que la expone exige una
    confirmación explícita y anota el resultado en la auditoría.
    """
    if not (images or volumes or containers):
        return {
            "success": False,
            "message": "No se indicó qué limpiar: marca images, volumes o containers.",
            "freed_bytes": 0,
        }
    try:
        client = _get_client()
        freed = 0
        summary: list[str] = []
        if containers:
            removed = client.containers.prune()
            freed += int(removed.get("SpaceReclaimed") or 0)
            summary.append(f"{len(removed.get('ContainersDeleted') or [])} contenedores parados")
        if images:
            removed = client.images.prune()
            freed += int(removed.get("SpaceReclaimed") or 0)
            summary.append(f"{len(removed.get('ImagesDeleted') or [])} imágenes")
        if volumes:
            removed = client.volumes.prune()
            freed += int(removed.get("SpaceReclaimed") or 0)
            summary.append(f"{len(removed.get('VolumesDeleted') or [])} volúmenes")
        message = "Limpieza completada: " + ", ".join(summary) + "."
        logger.info(message)
        return {"success": True, "message": message, "freed_bytes": freed}
    except (DockerException, APIError) as exc:
        message = f"Error limpiando Docker: {exc}"
        logger.error(message)
        return {"success": False, "message": message, "freed_bytes": 0}
