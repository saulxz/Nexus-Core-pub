"""Detección previa de servidores DHCP en la red (FASE 4).

Antes de activar el DHCP de dnsmasq se sondea la red con el script
``broadcast-dhcp-discover`` de nmap: si responde un servidor que no somos
nosotros, el panel se niega a activar el DHCP (evita concesiones corruptas
por dos servidores compitiendo).

En sistemas sin nmap (p. ej. desarrollo en Windows) la sonda devuelve
``available: False`` y el flujo de guardado continúa con un aviso.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

import psutil

from app.core.logging_conf import logger
from app.core.proc import run_hidden

_SERVER_RE = re.compile(r"Server Identifier:\s*(\d{1,3}(?:\.\d{1,3}){3})")


def _local_ipv4() -> set[str]:
    """Direcciones IPv4 locales (para distinguir nuestro DHCP del ajeno)."""
    addresses: set[str] = set()
    try:
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                family = getattr(addr.family, "name", str(addr.family))
                if family == "AF_INET" and addr.address:
                    addresses.add(addr.address)
    except Exception as exc:  # noqa: BLE001 - psutil puede fallar en entornos raros
        logger.warning("No se pudieron enumerar las IP locales: {}", exc)
    return addresses


def probe(timeout: int = 20) -> dict[str, Any]:
    """Sondea respondedores DHCP con nmap y clasifica propios/ajenos."""
    if shutil.which("nmap") is None:
        return {
            "available": False,
            "reason": "nmap no está disponible en este sistema.",
            "servers": [],
            "ours": [],
            "foreign": [],
            "verdict": "unknown",
        }
    try:
        completed = run_hidden(
            ["nmap", "-Pn", "--script", "broadcast-dhcp-discover"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "reason": f"La sonda DHCP falló: {exc}",
            "servers": [],
            "ours": [],
            "foreign": [],
            "verdict": "unknown",
        }

    text = (completed.stdout or "") + (completed.stderr or "")
    servers = sorted(set(_SERVER_RE.findall(text)))
    ours = _local_ipv4()
    own_servers = [server for server in servers if server in ours]
    foreign = [server for server in servers if server not in ours]
    return {
        "available": True,
        "reason": None,
        "servers": servers,
        "ours": own_servers,
        "foreign": foreign,
        "verdict": "ok" if not foreign else "conflict",
    }
