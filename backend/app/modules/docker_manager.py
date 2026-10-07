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
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app.core.config import get_settings
from app.core.logging_conf import logger

# Cliente Docker perezoso (singleton). Se crea en la primera llamada para no
# bloquear el arranque de la aplicación si el socket no está disponible.
_client: "docker.DockerClient | None" = None

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
        logger.error("No se pudieron listar los contenedores: {}", exc)
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
