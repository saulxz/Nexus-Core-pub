"""Edición guiada de configuración de servicios (FASE 3/4).

La web nunca edita ficheros a mano: el modelo estructurado (pydantic) se
renderiza, se escribe en el área de preparación y se aplica con el helper
``conf-apply``, que valida con la herramienta oficial del servicio, hace
copia de seguridad, reemplaza de forma atómica y recarga — con rollback
automático si algo falla.

Servicios soportados:
  * ``samba``   — smb.conf completo gestionado desde el panel.
  * ``dnsmasq`` — drop-in /etc/dnsmasq.d/nexus.conf con DNS y DHCP.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.core.audit import audit
from app.core.logging_conf import logger
from app.core.paths import staging_dir
from app.core.privileged import available as privileged_available, run_helper
from app.modules import dhcp_probe
from app.schemas.models import DnsmasqConfig, DnsmasqDhcpRange, SambaConfig

#: Marca que identifica un fichero gestionado por el panel.
MARKER = "# Gestionado por Nexus-Core"

TARGETS: dict[str, str] = {
    "samba": "/etc/samba/smb.conf",
    "dnsmasq": "/etc/dnsmasq.d/nexus.conf",
}

BACKUP_ROOT = Path("/var/backups/nexus")
LEASE_FILE = Path("/var/lib/misc/dnsmasq.leases")
MAX_RENDERED_BYTES = 64 * 1024

_MAP_TO_GUEST = {
    "never": "Never",
    "bad user": "Bad User",
    "bad password": "Bad Password",
}


# ================================ Render ================================
def render_samba(config: SambaConfig) -> str:
    """Genera el contenido completo de smb.conf a partir del modelo."""
    lines = [
        f"{MARKER} — ediciones desde el panel web",
        "# Los cambios manuales se perderán en la próxima edición desde el panel.",
        "",
        "[global]",
        f"   workgroup = {config.workgroup}",
        f"   server string = {config.server_string}",
        f"   map to guest = {config.map_to_guest}",
        "   # Por seguridad, solo se permite acceso desde redes privadas.",
        "   hosts allow = 127.0.0.1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16",
        "   hosts deny = 0.0.0.0/0",
        "",
    ]
    for share in config.shares:
        lines.append(f"[{share.name}]")
        if share.comment:
            lines.append(f"   comment = {share.comment}")
        lines.append(f"   path = {share.path}")
        lines.append(f"   read only = {'yes' if share.read_only else 'no'}")
        lines.append(f"   browseable = {'yes' if share.browseable else 'no'}")
        lines.append(f"   guest ok = {'yes' if share.guest_ok else 'no'}")
        if share.valid_users:
            lines.append(f"   valid users = {share.valid_users}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_dnsmasq(config: DnsmasqConfig) -> str:
    """Genera el contenido del drop-in de dnsmasq a partir del modelo."""
    lines = [f"{MARKER} — ediciones desde el panel web", ""]
    for server in config.servers:
        lines.append(f"server={server}")
    for record in config.records:
        lines.append(f"address=/{record.name}/{record.address}")
    if config.dhcp.enabled:
        lines.append("")
        lines.append("# DHCP")
        lines.append(
            f"dhcp-range={config.dhcp.start},{config.dhcp.end},{config.dhcp.lease_time}"
        )
    for reservation in config.reservations:
        if reservation.hostname:
            lines.append(
                f"dhcp-host={reservation.mac},{reservation.address},{reservation.hostname}"
            )
        else:
            lines.append(f"dhcp-host={reservation.mac},{reservation.address}")
    return "\n".join(lines).rstrip() + "\n"


def _render(service: str, config: SambaConfig | DnsmasqConfig) -> str:
    return render_samba(config) if service == "samba" else render_dnsmasq(config)


# ================================ Parseo ================================
def _parse_smb(text: str) -> SambaConfig:
    """Parsea un smb.conf generado por Nexus-Core (formato ini simple)."""
    sections: dict[str, dict[str, str]] = {}
    order: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            if current not in sections:
                sections[current] = {}
                order.append(current)
            continue
        if "=" in line and current:
            key, _, value = line.partition("=")
            sections[current][key.strip().lower()] = value.strip()

    global_map = sections.get("global", {})
    shares = []
    for name in order:
        if name.lower() == "global":
            continue
        params = sections[name]
        path = params.get("path", "")
        if not path:
            raise ValueError(f"La share '{name}' no tiene ruta (path).")
        shares.append(
            {
                "name": name,
                "path": path,
                "comment": params.get("comment", ""),
                "read_only": params.get("read only", "yes").lower()
                in ("yes", "true", "1"),
                "browseable": params.get("browseable", "yes").lower()
                in ("yes", "true", "1"),
                "guest_ok": params.get("guest ok", "no").lower()
                in ("yes", "true", "1"),
                "valid_users": params.get("valid users", ""),
            }
        )
    return SambaConfig(
        workgroup=global_map.get("workgroup", "WORKGROUP"),
        server_string=global_map.get("server string", "Nexus-Core Samba"),
        map_to_guest=_MAP_TO_GUEST.get(
            global_map.get("map to guest", "never").lower(), "Never"
        ),
        shares=shares,
    )


def _parse_dnsmasq(text: str) -> DnsmasqConfig:
    """Parsea el drop-in de dnsmasq generado por Nexus-Core."""
    servers: list[str] = []
    records: list[dict[str, str]] = []
    reservations: list[dict[str, str]] = []
    dhcp: dict[str, Any] = {"enabled": False}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("server="):
            servers.append(line[len("server="):].strip())
        elif line.startswith("address=/"):
            name, _, address = line[len("address=/"):].partition("/")
            records.append({"name": name.strip(), "address": address.strip()})
        elif line.startswith("dhcp-range="):
            parts = line[len("dhcp-range="):].split(",")
            if len(parts) >= 2:
                dhcp = {
                    "enabled": True,
                    "start": parts[0].strip(),
                    "end": parts[1].strip(),
                    "lease_time": parts[2].strip() if len(parts) > 2 else "12h",
                }
        elif line.startswith("dhcp-host="):
            parts = line[len("dhcp-host="):].split(",")
            reservations.append(
                {
                    "mac": parts[0].strip(),
                    "address": parts[1].strip() if len(parts) > 1 else "",
                    "hostname": parts[2].strip() if len(parts) > 2 else "",
                }
            )
    return DnsmasqConfig(
        servers=servers, records=records, dhcp=dhcp, reservations=reservations
    )


# ================================ Lectura ================================
def get_config(service: str) -> dict[str, Any]:
    """Estado y modelo de la configuración actual de un servicio."""
    target = TARGETS[service]
    path = Path(target)
    exists = path.is_file()
    managed = False
    model: dict[str, Any] | None = None
    reason: str | None = None

    if exists:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {
                "managed": False,
                "exists": True,
                "path": target,
                "config": None,
                "reason": f"No se pudo leer el fichero: {exc}",
            }
        if MARKER in text:
            try:
                parsed = (
                    _parse_smb(text) if service == "samba" else _parse_dnsmasq(text)
                )
                model = parsed.model_dump()
                managed = True
            except Exception as exc:  # noqa: BLE001 - fichero editado a mano
                logger.warning("Configuración de {} ilegible: {}", service, exc)
                reason = (
                    "El fichero existe pero no se pudo interpretar; "
                    "se muestra el modelo por defecto."
                )

    if model is None:
        default = SambaConfig() if service == "samba" else DnsmasqConfig()
        model = default.model_dump()
    return {
        "managed": managed,
        "exists": exists,
        "path": target,
        "config": model,
        "reason": reason,
    }


# ============================ Validación local ============================
def _run_native_validation(service: str, path: str) -> tuple[bool, str] | None:
    """Valida con la herramienta oficial del servicio si está disponible."""
    if service == "samba" and shutil.which("testparm"):
        args = ["testparm", "-s", path]
        tool = "testparm"
    elif service == "dnsmasq" and shutil.which("dnsmasq"):
        args = ["dnsmasq", "--test", f"--conf-file={path}"]
        tool = "dnsmasq --test"
    else:
        return None
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (False, f"No se pudo ejecutar el validador {tool}: {exc}")
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return (completed.returncode == 0, output[-400:])


def _local_warnings(service: str, config: Any) -> list[str]:
    """Avisos de seguridad/coherencia calculados sin tocar el sistema."""
    warnings: list[str] = []
    if service == "samba":
        for share in config.shares:
            if share.guest_ok and not share.valid_users:
                warnings.append(
                    f"La share '{share.name}' permite acceso invitado sin "
                    "restringir usuarios (guest ok = yes)."
                )
            if not share.read_only:
                warnings.append(
                    f"La share '{share.name}' es de escritura; revisa los permisos "
                    "del directorio en el sistema."
                )
    else:
        if config.dhcp.enabled:
            warnings.append(
                "El DHCP de dnsmasq repartirá direcciones en tu red; "
                "asegúrate de que no haya otro servidor DHCP activo."
            )
    return warnings


def validate_config(service: str, config: Any) -> dict[str, Any]:
    """Genera el borrador y lo valida localmente (sin aplicar nada)."""
    rendered = _render(service, config)
    warnings = _local_warnings(service, config)
    preview_path = staging_dir() / f"{service}.conf.preview"
    preview_path.write_text(rendered, encoding="utf-8")

    native = _run_native_validation(service, str(preview_path))
    if native is None:
        return {
            "ok": True,
            "message": (
                "Borrador generado. La validación con la herramienta oficial "
                "no está disponible en este sistema (se hará antes de aplicar)."
            ),
            "preview": rendered,
            "warnings": warnings,
        }
    ok, output = native
    return {
        "ok": ok,
        "message": (
            "Validación correcta con la herramienta oficial del servicio."
            if ok
            else f"La validación falló: {output}"
        ),
        "preview": rendered,
        "warnings": warnings,
    }


# ============================ Aplicación ============================
def _lease_warnings(dhcp: DnsmasqDhcpRange) -> list[str]:
    """Avisa de concesiones actuales fuera del nuevo rango."""
    if not dhcp.enabled or not LEASE_FILE.is_file():
        return []
    try:
        first = int(ipaddress.IPv4Address(dhcp.start))
        last = int(ipaddress.IPv4Address(dhcp.end))
        total = 0
        outside = 0
        for line in LEASE_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            total += 1
            try:
                address = int(ipaddress.IPv4Address(parts[2]))
            except ValueError:
                continue
            if not first <= address <= last:
                outside += 1
        if outside:
            return [
                f"{outside} de {total} concesiones actuales quedan fuera del nuevo "
                "rango; se renovarán solas en el siguiente ciclo."
            ]
    except (ValueError, OSError):
        return []
    return []


def _dhcp_preflight(config: DnsmasqConfig, actor: str, force: bool) -> list[str]:
    """Comprueba que no haya otro servidor DHCP antes de activar el nuestro."""
    warnings: list[str] = []
    if not force:
        probe = dhcp_probe.probe()
        if probe["available"] and probe["foreign"]:
            audit(
                "config.blocked",
                actor=actor,
                target="dnsmasq",
                result="denied",
                details={"dhcp_foraneo": probe["foreign"]},
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Se detectaron otros servidores DHCP en la red ("
                + ", ".join(probe["foreign"])
                + "). Activar el DHCP aquí podría corromper las concesiones. "
                "Forzar solo si estás seguro de que van a convivir.",
            )
        if not probe["available"]:
            warnings.append(
                "No se pudo comprobar si hay otros servidores DHCP: "
                + (probe.get("reason") or "sonda no disponible.")
            )
    warnings.extend(_lease_warnings(config.dhcp))
    return warnings


def _change_summary(service: str, config: Any) -> dict[str, Any]:
    if service == "samba":
        return {"shares": len(config.shares)}
    return {
        "servidores_dns": len(config.servers),
        "registros": len(config.records),
        "dhcp": bool(config.dhcp.enabled),
        "reservas": len(config.reservations),
    }


def save_config(
    service: str, config: Any, actor: str, force: bool = False
) -> dict[str, Any]:
    """Prepara el borrador, lo aplica con el helper y devuelve el resultado."""
    rendered = _render(service, config)
    if len(rendered.encode("utf-8")) > MAX_RENDERED_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "La configuración generada es demasiado grande (límite 64 KiB).",
        )

    warnings: list[str] = []
    if service == "dnsmasq" and config.dhcp.enabled:
        previous = get_config("dnsmasq")
        prev_enabled = bool(
            ((previous.get("config") or {}).get("dhcp") or {}).get("enabled")
        )
        if not prev_enabled:
            warnings.extend(_dhcp_preflight(config, actor, force))
        else:
            warnings.extend(_lease_warnings(config.dhcp))

    staging = staging_dir() / f"{service}.conf"
    staging.write_text(rendered, encoding="utf-8")
    try:
        os.chmod(staging, 0o600)
    except OSError:
        pass

    if not privileged_available():
        audit(
            "config.apply",
            actor=actor,
            target=service,
            result="unavailable",
            details={"motivo": "helpers privilegiados no disponibles"},
        )
        return {
            "success": False,
            "message": (
                "Aplicar configuración requiere Linux con los helpers de "
                "deploy/install.sh. El borrador quedó preparado en staging."
            ),
            "warnings": warnings,
            "preview": rendered,
        }

    result = run_helper("conf-apply", [service], timeout=90)
    audit(
        "config.apply",
        actor=actor,
        target=service,
        result="ok" if result["success"] else "fail",
        details={
            "salida": result["output"][-300:],
            "cambios": _change_summary(service, config),
        },
    )
    return {
        "success": result["success"],
        "message": result["output"]
        or ("Configuración aplicada." if result["success"] else "La aplicación falló."),
        "warnings": warnings,
        "preview": rendered,
    }


# ============================== Copias ==============================
def list_backups(service: str) -> list[dict[str, Any]]:
    """Lista las copias de seguridad disponibles de un servicio."""
    directory = BACKUP_ROOT / service
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    try:
        for entry in sorted(
            directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not entry.is_file():
                continue
            stat = entry.stat()
            items.append(
                {
                    "name": entry.name,
                    "size": stat.st_size,
                    "modified": dt.datetime.fromtimestamp(
                        stat.st_mtime, tz=dt.timezone.utc
                    ).isoformat(timespec="seconds"),
                }
            )
    except OSError as exc:
        logger.warning("No se pudieron listar las copias de {}: {}", service, exc)
    return items


def restore_backup(service: str, backup_name: str, actor: str) -> dict[str, Any]:
    """Restaura una copia de seguridad con el helper conf-restore."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", backup_name or ""):
        return {"success": False, "message": "Nombre de copia no válido."}
    if not privileged_available():
        return {
            "success": False,
            "message": "Restaurar copias requiere Linux con los helpers instalados.",
        }
    result = run_helper("conf-restore", [service, backup_name], timeout=90)
    audit(
        "config.restore",
        actor=actor,
        target=service,
        result="ok" if result["success"] else "fail",
        details={"copia": backup_name, "salida": result["output"][-300:]},
    )
    return {
        "success": result["success"],
        "message": result["output"]
        or ("Copia restaurada." if result["success"] else "La restauración falló."),
    }
