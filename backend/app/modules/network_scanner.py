"""Descubrimiento de hosts en la red local (módulo de redes y ciberseguridad).

Utiliza ``python-nmap`` para realizar un *ping sweep* (``nmap -sn``) sobre una
subred privada y devuelve la lista de hosts activos con su dirección MAC y
nombre si son accesibles. Incluye un *fallback* basado en la tabla de vecinos
ARP del núcleo (``ip neigh``) por si ``nmap`` no está instalado o falla.

Medidas de seguridad: solo se permite escanear rangos privados RFC1918 o la
interfaz de loopback, evitando que la plataforma se use como escáner de redes
de terceros.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess

from app.core.proc import run_hidden
from datetime import datetime, timezone
from typing import Any

import nmap

from app.core.config import get_settings
from app.core.logging_conf import logger


def _now_iso() -> str:
    """Devuelve la marca temporal actual en formato ISO8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _validate_subnet(subnet: str) -> ipaddress.IPv4Network:
    """Valida que la subred sea una red IPv4 privada o loopback.

    Args:
        subnet: Subred en notación CIDR (p. ej. ``192.168.1.0/24``).

    Returns:
        La red validada como ``IPv4Network``.

    Raises:
        ValueError: si el formato es inválido o la red no es privada.
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise ValueError(f"Subred inválida '{subnet}': {exc}") from exc

    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Solo se admiten redes IPv4.")

    if not (network.is_private or network.is_loopback):
        raise ValueError(
            "Por seguridad solo se permite escanear rangos privados (RFC1918) "
            "o loopback."
        )
    return network


def _fallback_arp_scan(subnet: str) -> list[dict[str, Any]]:
    """Escaneo de respaldo leyendo la tabla de vecinos ARP del sistema.

    Se usa cuando ``nmap`` no está disponible. No envía tráfico: solo lee las
    entradas ya presentes en la caché del núcleo.

    Args:
        subnet: Red en formato CIDR para filtrar las IPs.

    Returns:
        Lista de hosts detectados con estado ``up``.
    """
    hosts: list[dict[str, Any]] = []
    network = ipaddress.ip_network(subnet, strict=False)
    # Ejemplo de línea: 192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
    pattern = re.compile(
        r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+dev\s+(?P<dev>\S+)"
        r"(?:\s+lladdr\s+(?P<mac>[0-9a-fA-F:]{17}))?"
        r".*?(?P<state>REACHABLE|STALE|DELAY|PROBE|PERMANENT|FAILED|INCOMPLETE)?\s*$"
    )
    try:
        result = run_hidden(
            ["ip", "neigh", "show"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        for line in result.stdout.splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            ip = match.group("ip")
            try:
                if ipaddress.ip_address(ip) not in network:
                    continue
            except ValueError:
                continue
            mac = match.group("mac")
            state = (match.group("state") or "up").lower()
            hosts.append(
                {
                    "ip": ip,
                    "status": "up" if state not in ("failed", "incomplete") else "down",
                    "mac": mac.lower() if mac else None,
                    "hostname": None,
                }
            )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.error("El escaneo de respaldo ARP falló: {}", exc)
    return hosts


def scan_network(subnet: str | None = None) -> dict[str, Any]:
    """Escanea una subred privada y devuelve los hosts activos.

    Args:
        subnet: Subred CIDR a escanear. Si es ``None``, se usa
            ``SCAN_DEFAULT_SUBNET`` de la configuración.

    Returns:
        Diccionario con la subred, la lista de hosts activos, el recuento, la
        marca temporal y, en su caso, un mensaje de error.
    """
    settings = get_settings()
    target = subnet or settings.SCAN_DEFAULT_SUBNET

    # --- Validación de la subred ---
    try:
        network = _validate_subnet(target)
    except ValueError as exc:
        logger.warning("Subred rechazada: {}", exc)
        return {
            "subnet": target,
            "hosts": [],
            "count": 0,
            "scanned_at": _now_iso(),
            "error": str(exc),
        }

    detected: list[dict[str, Any]] = []

    # --- Intento principal con python-nmap ---
    try:
        scanner = nmap.PortScanner()
        logger.info("Iniciando escaneo nmap de la subred {}", network)
        scanner.scan(hosts=str(network), arguments="-sn -T4")
        for host in scanner.all_hosts():
            host_data = scanner[host]
            addresses = host_data.get("addresses", {}) or {}
            hostnames = host_data.get("hostnames") or []
            hostname = None
            if hostnames and hostnames[0].get("name"):
                hostname = hostnames[0]["name"]
            detected.append(
                {
                    "ip": host,
                    "status": host_data.state(),
                    "mac": addresses.get("mac"),
                    "hostname": hostname,
                }
            )
    except Exception as exc:  # noqa: BLE001 - nmap puede fallar por múltiples causas
        logger.warning("nmap no disponible o falló ({}). Usando respaldo ARP.", exc)
        detected = _fallback_arp_scan(str(network))

    # Solo se devuelven los hosts activos.
    active = [host for host in detected if host.get("status") == "up"]

    return {
        "subnet": str(network),
        "hosts": active,
        "count": len(active),
        "scanned_at": _now_iso(),
        "error": None,
    }
