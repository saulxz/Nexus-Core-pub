"""Configuración de red de la VM con watchdog anti-autobloqueo (FASE 6).

Permite cambiar la IP de la máquina desde la web SIN quedarse fuera: el
cambio se aplica con una reversión automática programada en una unidad
transitoria de systemd (``systemd-run``). Si el administrador no confirma
desde la nueva dirección antes del TTL, la red vuelve sola a como estaba.

Implementación soportada: **netplan** (Ubuntu / Debian moderno). Para otros
renderers (NetworkManager, ifupdown) el módulo lo detecta y lo indica, sin
aplicar nada.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from app.core.audit import audit
from app.core.logging_conf import logger
from app.core.paths import netguard_dir, staging_dir
from app.core.privileged import available as privileged_available, run_helper

#: Segundos que el administrador tiene para confirmar antes de la reversión.
DEFAULT_TTL = 90

NETPLAN_DROPIN = "/etc/netplan/90-nexus.yaml"
_TOKEN_RE = re.compile(r"^[a-f0-9]{12}$")


# ============================== Utilidades ==============================
def _run(args: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """Ejecuta un comando de solo lectura y devuelve (código, stdout)."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def detect_renderer() -> dict[str, Any]:
    """Detecta el gestor de red del sistema (y si lo gestiona cloud-init)."""
    cloud_init = Path("/etc/cloud").is_dir()
    if shutil.which("netplan") or Path("/etc/netplan").is_dir():
        return {"renderer": "netplan", "cloud_init": cloud_init, "supported": True}
    if shutil.which("nmcli"):
        return {
            "renderer": "networkmanager",
            "cloud_init": cloud_init,
            "supported": False,
        }
    if Path("/etc/network/interfaces").is_file():
        return {"renderer": "ifupdown", "cloud_init": cloud_init, "supported": False}
    return {"renderer": "desconocido", "cloud_init": cloud_init, "supported": False}


def get_state() -> dict[str, Any]:
    """Estado de red actual (interfaces, direcciones y puerta de enlace)."""
    info = detect_renderer()
    state: dict[str, Any] = {
        "available": shutil.which("ip") is not None,
        **info,
        "interfaces": [],
        "gateway": None,
        "default_interface": None,
        "dropin": NETPLAN_DROPIN,
        "dropin_exists": Path(NETPLAN_DROPIN).is_file(),
    }
    if not state["available"]:
        return state

    code, out = _run(["ip", "-j", "addr", "show"])
    if code == 0 and out.strip():
        try:
            for iface in json.loads(out):
                name = iface.get("ifname")
                if not name or name == "lo":
                    continue
                addresses: list[str] = []
                netmasks: list[str] = []
                cidrs: list[str] = []
                for item in iface.get("addr_info", []):
                    if item.get("family") != "inet":
                        continue
                    local = item.get("local")
                    prefix = item.get("prefixlen")
                    if not local:
                        continue
                    addresses.append(local)
                    if isinstance(prefix, int) and 0 <= prefix <= 32:
                        netmasks.append(
                            str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
                        )
                        cidrs.append(f"{local}/{prefix}")
                    else:
                        netmasks.append("—")
                        cidrs.append(local)
                state["interfaces"].append(
                    {
                        "name": name,
                        "mac": iface.get("address"),
                        "addresses": addresses,
                        "netmasks": netmasks,
                        "cidrs": cidrs,
                        "mtu": iface.get("mtu"),
                        "up": "UP" in (iface.get("flags") or []),
                    }
                )
        except json.JSONDecodeError:
            logger.warning("Salida de 'ip -j addr' no interpretable")

    code, out = _run(["ip", "-j", "route", "show", "default"])
    if code == 0 and out.strip():
        try:
            routes = json.loads(out)
            if routes:
                state["gateway"] = routes[0].get("gateway")
                state["default_interface"] = routes[0].get("dev")
        except json.JSONDecodeError:
            pass
    return state


# ============================ Plan y render ============================
def validate_plan(payload: Any) -> dict[str, Any]:
    """Valida un plan de cambio de IP (dirección, prefijo, puerta y DNS)."""
    warnings: list[str] = []
    try:
        address = ipaddress.IPv4Address(payload.address)
    except ValueError:
        return {"ok": False, "message": "Dirección IPv4 no válida.", "warnings": []}
    if not address.is_private:
        return {
            "ok": False,
            "message": "Solo se permiten direcciones privadas (RFC1918).",
            "warnings": [],
        }
    if not 8 <= int(payload.prefix) <= 30:
        return {
            "ok": False,
            "message": "El prefijo debe estar entre 8 y 30.",
            "warnings": [],
        }
    try:
        gateway = ipaddress.IPv4Address(payload.gateway)
        if not gateway.is_private:
            return {
                "ok": False,
                "message": "La puerta de enlace debe ser una dirección privada.",
                "warnings": [],
            }
    except ValueError:
        return {"ok": False, "message": "Puerta de enlace no válida.", "warnings": []}
    for server in payload.dns or []:
        try:
            ipaddress.IPv4Address(server)
        except ValueError:
            return {
                "ok": False,
                "message": f"Servidor DNS no válido: {server}",
                "warnings": [],
            }

    info = detect_renderer()
    state = get_state()
    if state["available"]:
        names = {item["name"] for item in state["interfaces"]}
        if payload.interface not in names:
            return {
                "ok": False,
                "message": (
                    f"La interfaz '{payload.interface}' no existe. "
                    f"Disponibles: {', '.join(sorted(names)) or 'ninguna'}."
                ),
                "warnings": [],
            }
        current = {
            addr for item in state["interfaces"] for addr in (item["addresses"] or [])
        }
        if payload.address in current:
            warnings.append(
                "La dirección indicada es la que ya tiene la interfaz (sin cambios)."
            )
        if state.get("gateway") and payload.gateway != state["gateway"]:
            warnings.append(
                f"La puerta de enlace actual es {state['gateway']}; "
                f"se cambiará a {payload.gateway}."
            )
    else:
        warnings.append(
            "No se pudo leer el estado real de la red en este sistema: "
            "validación limitada."
        )
    if not info["supported"]:
        warnings.append(
            "Solo se soporta netplan por ahora; renderer detectado: "
            f"{info['renderer']}. En este sistema no se podrá aplicar."
        )
    if info["cloud_init"]:
        warnings.append(
            "Se detectó cloud-init: si gestiona la red, revisa /etc/cloud para "
            "que no revierta los cambios al reiniciar."
        )
    warnings.append(
        f"Tras aplicar tendrás {DEFAULT_TTL} segundos para confirmar desde la "
        "dirección nueva; si no, la red se revierte sola."
    )
    return {"ok": True, "message": "Plan válido.", "warnings": warnings}


def render_netplan(payload: Any) -> str:
    """Renderiza el drop-in de netplan para el plan indicado."""
    lines = [
        "# Gestionado por Nexus-Core — cambio de IP con watchdog anti-autobloqueo",
        "network:",
        "  version: 2",
        "  ethernets:",
        f"    {payload.interface}:",
        "      dhcp4: false",
        f"      addresses: [{payload.address}/{payload.prefix}]",
        "      routes:",
        "        - to: default",
        f"          via: {payload.gateway}",
    ]
    if payload.dns:
        lines.append("      nameservers:")
        lines.append(f"        addresses: [{', '.join(payload.dns)}]")
    return "\n".join(lines) + "\n"


def plan(payload: Any) -> dict[str, Any]:
    """Previsualiza un cambio de IP: validación + fichero que se escribiría."""
    check = validate_plan(payload)
    return {
        "ok": check["ok"],
        "message": check["message"],
        "warnings": check.get("warnings", []),
        "preview": render_netplan(payload),
        "ttl": DEFAULT_TTL,
    }


# ============================ Aplicación ============================
def _state_file(token: str) -> Path:
    return netguard_dir() / f"{token}.json"


def _load_state(token: str) -> dict[str, Any] | None:
    path = _state_file(token)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(state: dict[str, Any]) -> None:
    path = _state_file(state["token"])
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def apply_change(payload: Any, actor: str) -> dict[str, Any]:
    """Aplica un plan de red con reversión automática si no se confirma."""
    check = validate_plan(payload)
    preview = render_netplan(payload)
    if not check["ok"]:
        return {
            "success": False,
            "message": check["message"],
            "warnings": check.get("warnings", []),
            "preview": preview,
            "token": None,
            "ttl": DEFAULT_TTL,
        }
    if not privileged_available() or not detect_renderer()["supported"]:
        return {
            "success": False,
            "message": (
                "Aplicar cambios de red requiere Linux con netplan y los helpers "
                "instalados (deploy/install.sh)."
            ),
            "warnings": check.get("warnings", []),
            "preview": preview,
            "token": None,
            "ttl": DEFAULT_TTL,
        }

    token = secrets.token_hex(6)
    staging = staging_dir() / f"net-{token}.yaml"
    staging.write_text(preview, encoding="utf-8")
    try:
        os.chmod(staging, 0o600)
    except OSError:
        pass

    result = run_helper("net-apply", [token, str(DEFAULT_TTL)], timeout=90)
    if result["success"]:
        _save_state(
            {
                "token": token,
                "interface": payload.interface,
                "address": payload.address,
                "prefix": payload.prefix,
                "gateway": payload.gateway,
                "deadline": time.time() + DEFAULT_TTL,
                "status": "pending",
                "actor": actor,
            }
        )
    audit(
        "network.apply",
        actor=actor,
        target=payload.interface,
        result="ok" if result["success"] else "fail",
        details={
            "nueva_ip": f"{payload.address}/{payload.prefix}",
            "salida": result["output"][-300:],
        },
    )
    return {
        "success": result["success"],
        "message": result["output"]
        or ("Plan aplicado." if result["success"] else "No se pudo aplicar el plan."),
        "warnings": check.get("warnings", []),
        "preview": preview,
        "token": token if result["success"] else None,
        "ttl": DEFAULT_TTL,
    }


def confirm_change(token: str, actor: str) -> dict[str, Any]:
    """Confirma el cambio y cancela la reversión automática."""
    if not _TOKEN_RE.fullmatch(token or ""):
        return {"success": False, "message": "Token no válido."}
    if not privileged_available():
        return {"success": False, "message": "Requiere Linux con los helpers instalados."}
    result = run_helper("net-confirm", [token], timeout=30)
    state = _load_state(token)
    if state:
        state["status"] = "confirmed" if result["success"] else state.get("status")
        _save_state(state)
    audit(
        "network.confirm",
        actor=actor,
        result="ok" if result["success"] else "fail",
        details={"token": token, "salida": result["output"][-200:]},
    )
    return {
        "success": result["success"],
        "message": result["output"]
        or ("Cambio confirmado." if result["success"] else "No se pudo confirmar."),
    }


def revert_change(token: str, actor: str) -> dict[str, Any]:
    """Revierte manualmente un cambio de red (adelanta el watchdog)."""
    if not _TOKEN_RE.fullmatch(token or ""):
        return {"success": False, "message": "Token no válido."}
    if not privileged_available():
        return {"success": False, "message": "Requiere Linux con los helpers instalados."}
    result = run_helper("net-revert", [token], timeout=60)
    state = _load_state(token)
    if state:
        state["status"] = "reverted" if result["success"] else state.get("status")
        _save_state(state)
    audit(
        "network.revert",
        actor=actor,
        result="ok" if result["success"] else "fail",
        details={"token": token, "salida": result["output"][-200:]},
    )
    return {
        "success": result["success"],
        "message": result["output"]
        or ("Cambio revertido." if result["success"] else "No se pudo revertir."),
    }
