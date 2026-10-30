"""Auditoría de seguridad local, extensa y de **solo lectura**.

El objetivo es responder a la pregunta «¿cómo está este host?» sin cambiar
nada: no escribe configuración, no reinicia servicios, no instala paquetes y
no modifica permisos. Solo lee el estado del sistema, ejecuta comandos de
consulta y devuelve hallazgos con severidad, evidencia y recomendación.

Bloques que cubre:

* **Sistema**: plataforma, actualizaciones pendientes, reinicio, time sync.
* **Identidad**: UID 0, contraseñas vacías, SSH, sudoers.
* **Archivos**: permisos de secretos, world-writable, SUID/SGID.
* **Servicios**: unidades fallidas y usuario del servicio Nexus.
* **Docker**: privilegios, red/PID del host, usuario root, `:latest`,
  montaje del socket y permisos del socket.
* **Red**: puertos en escucha, exposición en 0.0.0.0, Docker API y bases de
  datos expuestas.
* **Firewall**: ufw/nft en Linux; perfiles de Windows.
* **Aplicación**: 2FA, cookie Secure, hosts/CORS, cadena de auditoría,
  tokens, flota, backup y límites de la propia app.
* **Windows**: Defender, firewall, SMBv1, RDP y último hotfix.

Los comandos se ejecutan con listas de argumentos, sin shell, con timeout y
solo lectura. En Windows/Linux se omiten con un motivo explícito los checks
que no aplican.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from app.core import api_tokens, audit as audit_log, fleet, security
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.core.proc import run_hidden
from app.core.paths import audit_path, data_dir, db_path, setup_token_path

READ_ONLY = True
CACHE_TTL_SECONDS = 300.0
MAX_EVIDENCE = 320
BASELINE_NAME = "security_audit_baseline.json"

SEVERITIES = ("critical", "high", "medium", "low", "info")
STATUSES = ("fail", "warn", "pass", "unknown", "skipped")
WEIGHTS = {"critical": 25, "high": 15, "medium": 7, "low": 3, "info": 0}

SYSTEM = platform.system().lower()
IS_LINUX = SYSTEM == "linux"
IS_WINDOWS = SYSTEM == "windows"

_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_LOCK = threading.Lock()


@dataclass
class Finding:
    id: str
    category: str
    title: str
    severity: str
    status: str
    evidence: str
    recommendation: str
    reference: str = ""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _finding(
    check_id: str,
    category: str,
    title: str,
    severity: str,
    status: str,
    evidence: str,
    recommendation: str,
    reference: str = "",
) -> Finding:
    text = " ".join(str(evidence).split())
    if len(text) > MAX_EVIDENCE:
        text = text[: MAX_EVIDENCE - 1] + "…"
    return Finding(check_id, category, title, severity, status, text, recommendation, reference)


def _skip(check_id: str, category: str, title: str, reason: str) -> Finding:
    return _finding(
        check_id, category, title, "info", "skipped", reason,
        "No aplica en esta plataforma o el usuario del hub no tiene acceso.",
    )


def _unknown(check_id: str, category: str, title: str, reason: str) -> Finding:
    return _finding(
        check_id, category, title, "low", "unknown", reason,
        "Revisa el motivo; si el check requiere root, ejecútalo desde una cuenta con acceso.",
    )


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Ejecuta un comando de consulta sin shell y con timeout."""
    try:
        proc = run_hidden(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 127, "", str(exc)


def _ps(script: str, timeout: float = 10.0) -> tuple[int, str, str]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return 127, "", "PowerShell no disponible"
    return _run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _fmt_mode(mode: int | None) -> str:
    return f"0o{mode:04o}" if mode is not None else "?"


def _admin() -> str | None:
    try:
        name = security.admin_username()
        return name or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sistema
# ---------------------------------------------------------------------------
def check_platform() -> list[Finding]:
    return [
        _finding(
            "SYS-01", "Sistema", "Plataforma y kernel", "info", "pass",
            f"{platform.system()} {platform.release()} · {platform.version()} · "
            f"Python {platform.python_version()} · host {socket.gethostname()}",
            "Mantén el sistema, el kernel y las dependencias actualizados.",
            "CIS 1.1",
        )
    ]


def check_updates() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SYS-02", "Sistema", "Actualizaciones pendientes", "Solo se comprueba en Linux con apt.")]
    if not shutil.which("apt-get"):
        return [_skip("SYS-02", "Sistema", "Actualizaciones pendientes", "apt-get no está disponible.")]
    rc, out, err = _run(["apt-get", "-s", "upgrade"], timeout=25)
    if rc != 0:
        return [_unknown("SYS-02", "Sistema", "Actualizaciones pendientes", err or out or "apt-get falló.")]
    packages = [line for line in out.splitlines() if line.startswith("Inst ")]
    names = [line.split()[1] for line in packages[:10] if len(line.split()) > 1]
    count = len(packages)
    if count == 0:
        return [_finding(
            "SYS-02", "Sistema", "Actualizaciones pendientes", "low", "pass",
            "0 paquetes pendientes", "Sin acción.", "CIS 1.9",
        )]
    severity = "high" if count > 10 else "medium"
    status = "fail" if count > 10 else "warn"
    return [_finding(
        "SYS-02", "Sistema", "Actualizaciones pendientes", severity, status,
        f"{count} paquetes actualizables: {', '.join(names)}",
        "Aplica las actualizaciones de seguridad en una ventana de mantenimiento.",
        "CIS 1.9",
    )]


def check_reboot() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SYS-03", "Sistema", "Reinicio pendiente", "Solo aplica a Linux.")]
    if Path("/var/run/reboot-required").exists():
        return [_finding(
            "SYS-03", "Sistema", "Reinicio pendiente", "medium", "warn",
            "Existe /var/run/reboot-required",
            "Programa un reinicio para aplicar kernel o librerías actualizadas.",
            "CIS 1.9",
        )]
    return [_finding(
        "SYS-03", "Sistema", "Reinicio pendiente", "low", "pass",
        "No hay reinicio pendiente", "Sin acción.", "CIS 1.9",
    )]


def check_unattended() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SYS-04", "Sistema", "Actualizaciones automáticas", "Solo aplica a Linux.")]
    path = Path("/etc/apt/apt.conf.d/20auto-upgrades")
    if not path.exists():
        return [_finding(
            "SYS-04", "Sistema", "Actualizaciones automáticas", "medium", "warn",
            "No existe 20auto-upgrades",
            "Instala unattended-upgrades y activa las actualizaciones de seguridad.",
            "CIS 1.9",
        )]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_unknown("SYS-04", "Sistema", "Actualizaciones automáticas", str(exc))]
    enabled = 'Update-Package-Lists "1"' in text and 'Unattended-Upgrade "1"' in text
    return [_finding(
        "SYS-04", "Sistema", "Actualizaciones automáticas",
        "low" if enabled else "medium", "pass" if enabled else "warn",
        "unattended-upgrades activo" if enabled else "unattended-upgrades no está activo",
        "Mantén las actualizaciones de seguridad automáticas activas.",
        "CIS 1.9",
    )]


def check_time_sync() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SYS-05", "Sistema", "Sincronización horaria", "Solo aplica a Linux.")]
    if not shutil.which("timedatectl"):
        return [_skip("SYS-05", "Sistema", "Sincronización horaria", "timedatectl no disponible.")]
    rc, out, err = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if rc != 0:
        return [_unknown("SYS-05", "Sistema", "Sincronización horaria", err or out)]
    synced = out.strip().lower() == "yes"
    return [_finding(
        "SYS-05", "Sistema", "Sincronización horaria",
        "low", "pass" if synced else "warn",
        f"NTPSynchronized={out.strip()}",
        "Activa NTP/systemd-timesyncd: afecta a TOTP, TLS y auditoría.",
        "CIS 2.2",
    )]


# ---------------------------------------------------------------------------
# Identidad y acceso
# ---------------------------------------------------------------------------
def check_uid0() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("ID-01", "Identidad", "Cuentas UID 0", "Solo aplica a Linux.")]
    try:
        lines = Path("/etc/passwd").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [_unknown("ID-01", "Identidad", "Cuentas UID 0", str(exc))]
    roots = [line.split(":")[0] for line in lines if len(line.split(":")) > 2 and line.split(":")[2] == "0"]
    extra = [name for name in roots if name != "root"]
    if extra:
        return [_finding(
            "ID-01", "Identidad", "Cuentas UID 0", "critical", "fail",
            f"UID 0 además de root: {', '.join(extra)}",
            "Elimina o cambia el UID de las cuentas con privilegios de root.",
            "CIS 5.4",
        )]
    return [_finding(
        "ID-01", "Identidad", "Cuentas UID 0", "info", "pass",
        "Solo root tiene UID 0", "Sin acción.", "CIS 5.4",
    )]


def check_empty_passwords() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("ID-02", "Identidad", "Contraseñas vacías", "Solo aplica a Linux.")]
    try:
        lines = Path("/etc/shadow").read_text(encoding="utf-8", errors="replace").splitlines()
    except PermissionError:
        return [_unknown("ID-02", "Identidad", "Contraseñas vacías", "Sin permiso para leer /etc/shadow.")]
    except OSError as exc:
        return [_unknown("ID-02", "Identidad", "Contraseñas vacías", str(exc))]
    empty = []
    for line in lines:
        parts = line.split(":")
        if len(parts) > 1 and parts[1] == "":
            empty.append(parts[0])
    if empty:
        return [_finding(
            "ID-02", "Identidad", "Contraseñas vacías", "critical", "fail",
            f"Cuentas sin contraseña: {', '.join(empty)}",
            "Bloquea o asigna contraseña a esas cuentas inmediatamente.",
            "CIS 5.2",
        )]
    return [_finding(
        "ID-02", "Identidad", "Contraseñas vacías", "info", "pass",
        "Ninguna cuenta tiene contraseña vacía", "Sin acción.", "CIS 5.2",
    )]


def _ssh_config() -> dict[str, str]:
    if not IS_LINUX:
        return {}
    if shutil.which("sshd"):
        rc, out, _ = _run(["sshd", "-T"], timeout=6)
        if rc == 0:
            config: dict[str, str] = {}
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    config[parts[0].lower()] = parts[1].strip().lower()
            return config
    path = Path("/etc/ssh/sshd_config")
    if not path.exists():
        return {}
    config = {}
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                config[parts[0].lower()] = parts[1].strip().lower()
    except OSError:
        return {}
    return config


def check_ssh() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("ID-03", "Identidad", "Configuración SSH", "Solo aplica a Linux.")]
    cfg = _ssh_config()
    if not cfg:
        return [_skip("ID-03", "Identidad", "Configuración SSH", "OpenSSH no está instalado o no se pudo leer la configuración.")]
    out: list[Finding] = []
    root_login = cfg.get("permitrootlogin", "")
    if root_login == "yes":
        out.append(_finding(
            "ID-03", "Identidad", "SSH permite login de root", "high", "fail",
            "PermitRootLogin yes", "Configura PermitRootLogin no o prohibit-password.",
            "CIS 5.2.8",
        ))
    else:
        out.append(_finding(
            "ID-03", "Identidad", "SSH permite login de root", "low", "pass",
            f"PermitRootLogin {root_login or 'no definido'}",
            "Mantén el acceso directo de root deshabilitado.", "CIS 5.2.8",
        ))
    password_auth = cfg.get("passwordauthentication", "")
    if password_auth == "yes":
        out.append(_finding(
            "ID-04", "Identidad", "SSH con contraseña", "medium", "warn",
            "PasswordAuthentication yes",
            "Prioriza claves SSH y desactiva la autenticación por contraseña.",
            "CIS 5.2.10",
        ))
    else:
        out.append(_finding(
            "ID-04", "Identidad", "SSH con contraseña", "low", "pass",
            f"PasswordAuthentication {password_auth or 'no definido'}",
            "Sin acción.", "CIS 5.2.10",
        ))
    out.append(_finding(
        "ID-05", "Identidad", "Puerto SSH", "info", "pass",
        f"Port {cfg.get('port', '22')}",
        "Si cambias el puerto, actualiza firewall y monitorización.",
        "CIS 5.2",
    ))
    x11 = cfg.get("x11forwarding", "")
    out.append(_finding(
        "ID-06", "Identidad", "X11 forwarding", "low" if x11 != "yes" else "medium",
        "pass" if x11 != "yes" else "warn",
        f"X11Forwarding {x11 or 'no definido'}",
        "Desactiva X11Forwarding si no lo necesitas.", "CIS 5.2.6",
    ))
    return out


def check_sudoers() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("ID-07", "Identidad", "Reglas sudoers", "Solo aplica a Linux.")]
    if not shutil.which("grep"):
        return [_skip("ID-07", "Identidad", "Reglas sudoers", "grep no disponible.")]
    rc, out, err = _run(
        ["grep", "-R", "-n", "NOPASSWD", "/etc/sudoers", "/etc/sudoers.d"],
        timeout=6,
    )
    if rc == 1:
        return [_finding(
            "ID-07", "Identidad", "Reglas sudoers", "low", "pass",
            "Sin reglas NOPASSWD", "Sin acción.", "CIS 5.3",
        )]
    if rc != 0:
        return [_unknown("ID-07", "Identidad", "Reglas sudoers", err or "No se pudo leer sudoers.")]
    lines = [line for line in out.splitlines() if line.strip()]
    broad = [line for line in lines if "NOPASSWD: ALL" in line.upper() or "NOPASSWD:ALL" in line.upper()]
    if broad:
        return [_finding(
            "ID-07", "Identidad", "Reglas sudoers", "critical", "fail",
            f"NOPASSWD amplio: {broad[0][:180]}",
            "Sustituye NOPASSWD: ALL por comandos exactos y valida con visudo.",
            "CIS 5.3",
        )]
    outside = [line for line in lines if "/etc/sudoers.d/nexus" not in line]
    if outside:
        return [_finding(
            "ID-07", "Identidad", "Reglas sudoers", "medium", "warn",
            f"{len(outside)} reglas NOPASSWD fuera del archivo de Nexus",
            "Revisa cada regla y limita los comandos autorizados.",
            "CIS 5.3",
        )]
    return [_finding(
        "ID-07", "Identidad", "Reglas sudoers", "low", "pass",
        f"Solo los helpers de Nexus usan NOPASSWD ({len(lines)} reglas)",
        "Mantén la lista exacta de helpers.", "CIS 5.3",
    )]


# ---------------------------------------------------------------------------
# Archivos y permisos
# ---------------------------------------------------------------------------
def check_sensitive_files() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("FILE-01", "Archivos", "Permisos de secretos", "Los permisos POSIX no aplican en esta plataforma.")]
    targets = [
        ("FILE-01", Path("/etc/nexus/nexus.env"), "Entorno de producción"),
        ("FILE-02", Path("/etc/nexus/terminal_key"), "Clave SSH de la terminal"),
        ("FILE-03", Path("/etc/nexus/terminal_known_hosts"), "known_hosts de la terminal"),
        ("FILE-04", data_dir(), "Directorio de datos"),
        ("FILE-05", db_path(), "Base de datos SQLite"),
        ("FILE-06", setup_token_path(), "Token de primer arranque"),
        ("FILE-07", audit_path(), "Registro de auditoría"),
    ]
    out: list[Finding] = []
    for check_id, path, label in targets:
        if not path.exists():
            out.append(_skip(check_id, "Archivos", label, f"No existe {path}."))
            continue
        mode = _mode(path)
        if mode is None:
            out.append(_unknown(check_id, "Archivos", label, f"No se pudo leer el modo de {path}."))
            continue
        exposed = bool(mode & 0o077)
        out.append(_finding(
            check_id, "Archivos", label,
            "high" if exposed else "low",
            "fail" if exposed else "pass",
            f"{path} · {_fmt_mode(mode)}",
            "Restringe el permiso a 0600 (archivos) o 0700 (directorios).",
            "CIS 3.1",
        ))
    return out


def check_world_writable() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("FILE-08", "Archivos", "Archivos world-writable", "Solo aplica a Linux.")]
    if not shutil.which("find"):
        return [_skip("FILE-08", "Archivos", "Archivos world-writable", "find no disponible.")]
    paths = [str(p) for p in (data_dir(), Path("/etc/nexus"), Path("/opt/nexus-core")) if p.exists()]
    if not paths:
        return [_skip("FILE-08", "Archivos", "Archivos world-writable", "No existen los directorios a revisar.")]
    rc, out, err = _run(["find", *paths, "-maxdepth", "3", "-type", "f", "-perm", "-0002"], timeout=10)
    if rc != 0:
        return [_unknown("FILE-08", "Archivos", "Archivos world-writable", err or "find falló.")]
    files = [line for line in out.splitlines() if line.strip()]
    if files:
        return [_finding(
            "FILE-08", "Archivos", "Archivos world-writable", "high", "fail",
            f"{len(files)} archivos: {', '.join(files[:6])}",
            "Quita el bit de escritura para otros (chmod o-w).",
            "CIS 3.1",
        )]
    return [_finding(
        "FILE-08", "Archivos", "Archivos world-writable", "low", "pass",
        "No hay archivos world-writable en las rutas revisadas", "Sin acción.", "CIS 3.1",
    )]


def check_suid() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("FILE-09", "Archivos", "Binarios SUID/SGID", "Solo aplica a Linux.")]
    if not shutil.which("find"):
        return [_skip("FILE-09", "Archivos", "Binarios SUID/SGID", "find no disponible.")]
    paths = [str(p) for p in (Path("/opt/nexus-core"), Path("/usr/local/lib/nexus")) if p.exists()]
    if not paths:
        return [_skip("FILE-09", "Archivos", "Binarios SUID/SGID", "No existen las rutas del proyecto.")]
    rc, out, err = _run(["find", *paths, "-xdev", "-type", "f", "-perm", "/6000"], timeout=10)
    if rc != 0:
        return [_unknown("FILE-09", "Archivos", "Binarios SUID/SGID", err or "find falló.")]
    files = [line for line in out.splitlines() if line.strip()]
    if files:
        return [_finding(
            "FILE-09", "Archivos", "Binarios SUID/SGID", "medium", "warn",
            f"{len(files)} binarios: {', '.join(files[:6])}",
            "Revisa si esos binarios necesitan SUID/SGID; los helpers no deberían.",
            "CIS 6.1",
        )]
    return [_finding(
        "FILE-09", "Archivos", "Binarios SUID/SGID", "low", "pass",
        "Sin binarios SUID/SGID en las rutas del proyecto", "Sin acción.", "CIS 6.1",
    )]


# ---------------------------------------------------------------------------
# Servicios y Docker
# ---------------------------------------------------------------------------
def check_systemd_failed() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SVC-01", "Servicios", "Unidades systemd fallidas", "Solo aplica a Linux.")]
    if not shutil.which("systemctl"):
        return [_skip("SVC-01", "Servicios", "Unidades systemd fallidas", "systemctl no disponible.")]
    rc, out, err = _run(["systemctl", "--failed", "--no-legend", "--plain"], timeout=8)
    if rc != 0:
        return [_unknown("SVC-01", "Servicios", "Unidades systemd fallidas", err or "systemctl falló.")]
    failed = [line for line in out.splitlines() if line.strip()]
    if failed:
        return [_finding(
            "SVC-01", "Servicios", "Unidades systemd fallidas", "high", "fail",
            f"{len(failed)} unidades: {', '.join(line.split()[0] for line in failed[:6])}",
            "Revisa journalctl -u <unidad> y corrige la causa raíz.",
            "CIS 1.5",
        )]
    return [_finding(
        "SVC-01", "Servicios", "Unidades systemd fallidas", "low", "pass",
        "Sin unidades fallidas", "Sin acción.", "CIS 1.5",
    )]


def check_nexus_service() -> list[Finding]:
    if not IS_LINUX:
        return [_skip("SVC-02", "Servicios", "Usuario del servicio Nexus", "Solo aplica a Linux.")]
    if not shutil.which("systemctl"):
        return [_skip("SVC-02", "Servicios", "Usuario del servicio Nexus", "systemctl no disponible.")]
    rc, out, err = _run(["systemctl", "show", "nexus", "-p", "User", "--value"], timeout=6)
    if rc != 0 or not out.strip():
        return [_skip("SVC-02", "Servicios", "Usuario del servicio Nexus", "El servicio nexus no está instalado o no expone User.")]
    user = out.strip()
    bad = user in ("root", "0")
    return [_finding(
        "SVC-02", "Servicios", "Usuario del servicio Nexus",
        "high" if bad else "low", "fail" if bad else "pass",
        f"User={user}",
        "Ejecuta nexus.service con el usuario de sistema nexus, no como root.",
        "CIS 5.4",
    )]


def _docker_client():
    if IS_LINUX and not Path("/var/run/docker.sock").exists():
        return None
    if IS_WINDOWS and not Path("//./pipe/docker_engine").exists():
        return None
    try:
        import docker  # import perezoso: no bloquea el arranque si no está
        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


def check_docker() -> list[Finding]:
    client = _docker_client()
    if client is None:
        return [_skip(
            "DOCKER-01", "Docker", "Inventario Docker",
            "Docker no está instalado, el daemon no responde o el socket no es accesible.",
        )]
    try:
        containers = client.containers.list(all=True)
    except Exception as exc:
        return [_unknown("DOCKER-01", "Docker", "Inventario Docker", f"No se pudo listar: {exc}")]

    out: list[Finding] = [
        _finding(
            "DOCKER-01", "Docker", "Inventario Docker", "info", "pass",
            f"{len(containers)} contenedores", "Revisa periódicamente la lista.",
            "CIS 4.1",
        )
    ]
    privileged, host_net, host_pid, root_user, latest, sock_mount = [], [], [], [], [], []
    for container in containers:
        attrs = container.attrs or {}
        host = attrs.get("HostConfig", {}) or {}
        config = attrs.get("Config", {}) or {}
        name = (container.name or "").lstrip("/")
        if host.get("Privileged"):
            privileged.append(name)
        if (host.get("NetworkMode") or "") == "host":
            host_net.append(name)
        if (host.get("PidMode") or "") == "host":
            host_pid.append(name)
        user = str(config.get("User") or "").strip().lower()
        if user in ("", "0", "root", "0:0"):
            root_user.append(name)
        image = str(config.get("Image") or "")
        if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image):
            latest.append(f"{name} ({image or '?'})")
        for mount in attrs.get("Mounts", []) or []:
            if "docker.sock" in str(mount.get("Source", "")):
                sock_mount.append(name)

    if privileged:
        out.append(_finding(
            "DOCKER-02", "Docker", "Contenedores privilegiados", "high", "fail",
            f"{len(privileged)}: {', '.join(privileged[:6])}",
            "Quita --privileged y concede solo las capacidades necesarias.",
            "CIS 4.1",
        ))
    if host_net:
        out.append(_finding(
            "DOCKER-03", "Docker", "Contenedores con red host", "high", "fail",
            f"{len(host_net)}: {', '.join(host_net[:6])}",
            "Usa redes bridge dedicadas salvo que sea imprescindible.",
            "CIS 4.1",
        ))
    if host_pid:
        out.append(_finding(
            "DOCKER-04", "Docker", "Contenedores con PID host", "high", "fail",
            f"{len(host_pid)}: {', '.join(host_pid[:6])}",
            "Quita --pid=host; el contenedor no debe ver procesos del host.",
            "CIS 4.1",
        ))
    if root_user:
        out.append(_finding(
            "DOCKER-05", "Docker", "Contenedores como root", "medium", "warn",
            f"{len(root_user)}: {', '.join(root_user[:6])}",
            "Define USER no privilegiado en la imagen o en el despliegue.",
            "CIS 4.1",
        ))
    if latest:
        out.append(_finding(
            "DOCKER-06", "Docker", "Imágenes sin versión fija", "low", "warn",
            f"{len(latest)}: {', '.join(latest[:6])}",
            "Fija una etiqueta o un digest para despliegues reproducibles.",
            "CIS 4.1",
        ))
    if sock_mount:
        out.append(_finding(
            "DOCKER-07", "Docker", "Socket Docker montado", "high", "fail",
            f"{len(sock_mount)}: {', '.join(sock_mount[:6])}",
            "Montar docker.sock equivale a root sobre el host; evítalo.",
            "CIS 4.1",
        ))
    if IS_LINUX:
        sock = Path("/var/run/docker.sock")
        if sock.exists():
            mode = _mode(sock)
            if mode is not None:
                world = bool(mode & 0o002)
                out.append(_finding(
                    "DOCKER-08", "Docker", "Permisos del socket Docker",
                    "critical" if world else "low",
                    "fail" if world else "pass",
                    f"/var/run/docker.sock · {_fmt_mode(mode)}",
                    "El socket debe ser 0660 root:docker, nunca world-writable.",
                    "CIS 4.1",
                ))
    return out


# ---------------------------------------------------------------------------
# Red y firewall
# ---------------------------------------------------------------------------
def check_nmap() -> list[Finding]:
    """Escaneo activo y opcional de loopback con nmap (sin scripts ni exploits)."""
    if not shutil.which("nmap"):
        return [_skip(
            "NET-05", "Red", "Escaneo nmap de loopback",
            "nmap no está instalado; el check de puertos con psutil sigue activo.",
        )]
    rc, out, err = _run(
        ["nmap", "-sT", "-Pn", "--top-ports", "100", "127.0.0.1", "-oG", "-"],
        timeout=25,
    )
    if rc != 0:
        return [_unknown("NET-05", "Red", "Escaneo nmap de loopback", err or "nmap falló.")]
    ports = re.findall(r"(\d+)/open/tcp//([^/]*)", out)
    if not ports:
        return [_finding(
            "NET-05", "Red", "Escaneo nmap de loopback", "info", "pass",
            "Sin puertos abiertos en el top-100 de 127.0.0.1",
            "Sin acción.", "CIS 9.1",
        )]
    detail = ", ".join(f"{port}/{service or '?'}" for port, service in ports[:12])
    return [_finding(
        "NET-05", "Red", "Escaneo nmap de loopback",
        "low" if len(ports) <= 5 else "warn",
        "pass" if len(ports) <= 5 else "warn",
        f"{len(ports)} puertos abiertos: {detail}",
        "Revisa que cada servicio sea necesario y esté en localhost o con firewall.",
        "CIS 9.1",
    )]


def check_ports() -> list[Finding]:
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError) as exc:
        return [_unknown("NET-01", "Red", "Puertos en escucha", f"No se pudo leer la tabla de conexiones: {exc}")]
    listening = [c for c in conns if c.status == psutil.CONN_LISTEN and c.laddr]
    unique: dict[tuple[str, int], str] = {}
    for conn in listening:
        ip = str(getattr(conn.laddr, "ip", "") or "")
        port = int(getattr(conn.laddr, "port", 0) or 0)
        if not port:
            continue
        unique[(ip, port)] = ip
    if not unique:
        return [_finding(
            "NET-01", "Red", "Puertos en escucha", "info", "pass",
            "Sin puertos en escucha detectados", "Sin acción.", "CIS 9.1",
        )]
    listing = ", ".join(f"{ip}:{port}" for (ip, port) in sorted(unique)[:12])
    out = [_finding(
        "NET-01", "Red", "Puertos en escucha", "info", "pass",
        f"{len(unique)} puertos: {listing}",
        "Revisa que cada puerto sea necesario y esté detrás del firewall.",
        "CIS 9.1",
    )]
    exposed = sorted((ip, port) for (ip, port) in unique if ip in ("0.0.0.0", "::", ""))
    sensitive = {22, 2375, 2376, 5432, 3306, 6379, 8000, 8780, 8080}
    hit = [(ip, port) for (ip, port) in exposed if port in sensitive]
    if exposed:
        out.append(_finding(
            "NET-02", "Red", "Servicios en todas las interfaces",
            "high" if hit else "medium",
            "fail" if hit else "warn",
            ", ".join(f"{ip}:{port}" for (ip, port) in exposed[:12]),
            "Enlaza los servicios a 127.0.0.1 o a una interfaz concreta y usa el firewall.",
            "CIS 9.1",
        ))
    if any(port in (2375, 2376) for (_, port) in unique):
        out.append(_finding(
            "NET-03", "Red", "Docker API expuesta", "critical", "fail",
            "Puerto 2375/2376 en escucha",
            "No expongas la API de Docker sin TLS y autenticación.",
            "CIS 4.1",
        ))
    db_ports = {5432, 3306, 6379}
    if any(port in db_ports for (_, port) in exposed):
        out.append(_finding(
            "NET-04", "Red", "Base de datos expuesta", "high", "fail",
            ", ".join(f"{ip}:{port}" for (ip, port) in exposed if port in db_ports),
            "Limita la base de datos a localhost o a la red interna.",
            "CIS 9.1",
        ))
    return out


def check_firewall() -> list[Finding]:
    if IS_LINUX:
        if shutil.which("ufw"):
            rc, out, err = _run(["ufw", "status"], timeout=6)
            if rc == 0 and "error" not in (out + err).lower():
                active = "status: active" in out.lower()
                return [_finding(
                    "FW-01", "Firewall", "Firewall activo",
                    "low" if active else "high",
                    "pass" if active else "fail",
                    "ufw activo" if active else "ufw inactivo",
                    "Activa ufw y permite solo SSH/HTTPS.",
                    "CIS 3.5",
                )]
        if shutil.which("nft"):
            rc, out, err = _run(["nft", "list", "ruleset"], timeout=6)
            if rc == 0 and "operation not permitted" not in (out + err).lower():
                enabled = bool(out.strip())
                return [_finding(
                    "FW-01", "Firewall", "Firewall activo",
                    "low" if enabled else "medium",
                    "pass" if enabled else "warn",
                    "nftables con reglas" if enabled else "nftables sin reglas",
                    "Define una política de entrada restrictiva.",
                    "CIS 3.5",
                )]
        return [_unknown("FW-01", "Firewall", "Firewall activo", "Sin permisos para consultar ufw/nftables.")]
    if IS_WINDOWS:
        rc, out, err = _ps(
            "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json -Compress"
        )
        if rc != 0 or not out:
            return [_skip("FW-01", "Firewall", "Firewall activo", err or "No se pudo consultar el firewall.")]
        try:
            data = json.loads(out)
            profiles = data if isinstance(data, list) else [data]
            enabled = all(bool(item.get("Enabled")) for item in profiles)
        except (json.JSONDecodeError, AttributeError):
            return [_unknown("FW-01", "Firewall", "Firewall activo", "Respuesta de PowerShell no interpretable.")]
        return [_finding(
            "FW-01", "Firewall", "Firewall activo",
            "low" if enabled else "high",
            "pass" if enabled else "fail",
            f"{len(profiles)} perfiles; todos activos" if enabled else "Algún perfil está desactivado",
            "Activa el firewall en los perfiles de dominio, privado y público.",
            "CIS 3.5",
        )]
    return [_skip("FW-01", "Firewall", "Firewall activo", "Plataforma no soportada.")]


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------
def check_app_security() -> list[Finding]:
    settings = get_settings()
    out: list[Finding] = []
    admin = _admin()

    if admin:
        state = security.get_totp_state(admin)
        enabled = bool(state.get("enabled"))
        out.append(_finding(
            "APP-01", "Aplicación", "Segundo factor (TOTP)",
            "high" if not enabled else "low",
            "fail" if not enabled else "pass",
            f"2FA {'activo' if enabled else 'desactivado'} para {admin}",
            "Activa TOTP y guarda los códigos de recuperación fuera del servidor.",
            "CIS 5.4",
        ))
    else:
        out.append(_skip("APP-01", "Aplicación", "Segundo factor (TOTP)", "No hay administrador configurado."))

    secure = bool(settings.SESSION_COOKIE_SECURE)
    out.append(_finding(
        "APP-02", "Aplicación", "Cookie de sesión Secure",
        "low" if secure else "high",
        "pass" if secure else "warn",
        f"SESSION_COOKIE_SECURE={secure}",
        "Actívalo cuando el panel se sirva por HTTPS (túnel o Caddy).",
        "CIS 3.1",
    ))

    hosts = settings.ALLOWED_HOSTS.strip()
    wildcard = hosts in ("", "*")
    out.append(_finding(
        "APP-03", "Aplicación", "Hosts permitidos",
        "medium" if wildcard else "low",
        "warn" if wildcard else "pass",
        f"ALLOWED_HOSTS={hosts or '(vacío)'}",
        "Fija el dominio real y localhost en producción.",
        "CIS 3.1",
    ))

    origins = settings.ALLOWED_ORIGINS.strip()
    if origins == "*":
        out.append(_finding(
            "APP-04", "Aplicación", "CORS", "medium", "warn",
            "ALLOWED_ORIGINS=*", "Deja CORS vacío (same-origin) salvo necesidad explícita.",
            "CIS 3.1",
        ))
    else:
        out.append(_finding(
            "APP-04", "Aplicación", "CORS", "low", "pass",
            "CORS same-origin" if not origins else f"Orígenes: {origins}",
            "Mantén la lista mínima.", "CIS 3.1",
        ))

    idle = int(settings.SESSION_IDLE_MINUTES)
    absolute = int(settings.SESSION_ABSOLUTE_HOURS)
    good = idle <= 60 and absolute <= 12
    out.append(_finding(
        "APP-05", "Aplicación", "Caducidad de sesión",
        "low" if good else "medium",
        "pass" if good else "warn",
        f"idle={idle} min · absoluta={absolute} h",
        "Mantén la inactividad en 30 min y la absoluta en 8 h.",
        "CIS 5.4",
    ))

    chain = audit_log.verify_chain()
    valid = bool(chain.get("valid"))
    out.append(_finding(
        "APP-06", "Aplicación", "Cadena de auditoría",
        "critical" if not valid else "low",
        "fail" if not valid else "pass",
        f"{chain.get('events', 0)} eventos · {'íntegra' if valid else chain.get('error')}",
        "Investiga cualquier ruptura; revisa permisos del archivo y del usuario nexus.",
        "NIST SP 800-92",
    ))

    tokens = api_tokens.list_tokens()
    expired = [t for t in tokens if t.get("expired")]
    out.append(_finding(
        "APP-07", "Aplicación", "Tokens de API",
        "medium" if expired else "info",
        "warn" if expired else "pass",
        f"{len(tokens)} tokens · {len(expired)} caducados",
        "Revoca los tokens caducados o que ya no se usen.",
        "CIS 5.4",
    ))

    try:
        hosts_list = fleet.list_hosts()
        disabled = [h for h in hosts_list if h.get("enabled") is False]
        out.append(_finding(
            "APP-08", "Aplicación", "Flota de agentes",
            "info", "warn" if disabled else "pass",
            f"{len(hosts_list)} hosts · {len(disabled)} deshabilitados",
            "Revisa hosts deshabilitados y rota tokens si un equipo se retira.",
            "CIS 5.4",
        ))
    except Exception as exc:
        out.append(_unknown("APP-08", "Aplicación", "Flota de agentes", str(exc)))

    if IS_LINUX and shutil.which("systemctl"):
        rc, out_txt, _ = _run(["systemctl", "is-active", "nexus-backup.timer"], timeout=5)
        active = rc == 0 and out_txt.strip() == "active"
        out.append(_finding(
            "APP-09", "Aplicación", "Backup programado",
            "low" if active else "medium",
            "pass" if active else "warn",
            f"nexus-backup.timer={out_txt.strip() or 'no encontrado'}",
            "Instala y prueba el timer de backup; verifica una restauración.",
            "CIS 11.1",
        ))
    else:
        out.append(_skip("APP-09", "Aplicación", "Backup programado", "Solo aplica a Linux con systemd."))

    out.append(_finding(
        "APP-10", "Aplicación", "Límite de enrolamiento",
        "low", "pass",
        "20 intentos por IP cada 15 min (middleware en memoria)",
        "Si el hub crece, sustituye el límite en memoria por uno distribuido.",
        "CIS 5.4",
    ))
    return out


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def check_windows() -> list[Finding]:
    if not IS_WINDOWS:
        return [_skip("WIN-01", "Windows", "Controles de Windows", "Solo aplica a Windows.")]
    out: list[Finding] = []

    rc, txt, err = _ps(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled | ConvertTo-Json -Compress"
    )
    if rc != 0 or not txt:
        out.append(_skip("WIN-01", "Windows", "Microsoft Defender", err or "No se pudo consultar Defender."))
    else:
        try:
            data = json.loads(txt)
            ok = bool(data.get("AntivirusEnabled")) and bool(data.get("RealTimeProtectionEnabled"))
            out.append(_finding(
                "WIN-01", "Windows", "Microsoft Defender",
                "high" if not ok else "low",
                "fail" if not ok else "pass",
                f"Antivirus={data.get('AntivirusEnabled')} · tiempo real={data.get('RealTimeProtectionEnabled')}",
                "Activa Defender y la protección en tiempo real.",
                "CIS 8.1",
            ))
        except json.JSONDecodeError:
            out.append(_unknown("WIN-01", "Windows", "Microsoft Defender", "Respuesta no interpretable."))

    rc, txt, err = _ps("(Get-SmbServerConfiguration).EnableSMB1Protocol")
    if rc == 0 and txt:
        enabled = txt.strip().lower() == "true"
        out.append(_finding(
            "WIN-02", "Windows", "SMBv1", "high" if enabled else "low",
            "fail" if enabled else "pass",
            f"EnableSMB1Protocol={enabled}",
            "Desactiva SMBv1; es un protocolo obsoleto y vulnerable.",
            "CIS 2.3.1.1",
        ))
    else:
        out.append(_skip("WIN-02", "Windows", "SMBv1", err or "No se pudo consultar SMB."))

    rc, txt, err = _ps(
        "(Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server').fDenyTSConnections"
    )
    if rc == 0 and txt:
        denied = txt.strip() == "1"
        out.append(_finding(
            "WIN-03", "Windows", "Escritorio remoto (RDP)",
            "low" if denied else "medium",
            "pass" if denied else "warn",
            f"fDenyTSConnections={txt.strip()}",
            "Desactiva RDP si no lo necesitas; si lo usas, exige VPN y MFA.",
            "CIS 2.3.2",
        ))
    else:
        out.append(_skip("WIN-03", "Windows", "Escritorio remoto (RDP)", err or "No se pudo consultar RDP."))

    rc, txt, err = _ps(
        "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1 HotFixID,InstalledOn | ConvertTo-Json -Compress"
    )
    if rc == 0 and txt:
        out.append(_finding(
            "WIN-04", "Windows", "Último parche instalado", "info", "pass",
            txt[:220], "Comprueba que Windows Update sigue activo.", "CIS 1.9",
        ))
    else:
        out.append(_skip("WIN-04", "Windows", "Último parche instalado", err or "No se pudo consultar Get-HotFix."))
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _checks():
    checks = [
        check_platform,
        check_updates,
        check_reboot,
        check_unattended,
        check_time_sync,
        check_uid0,
        check_empty_passwords,
        check_ssh,
        check_sudoers,
        check_sensitive_files,
        check_world_writable,
        check_suid,
        check_systemd_failed,
        check_nexus_service,
        check_docker,
        check_ports,
        check_nmap,
        check_firewall,
        check_app_security,
    ]
    if IS_WINDOWS:
        checks.insert(1, check_windows)
    return checks


def _score(findings: list[Finding]) -> int:
    score = 100
    for finding in findings:
        if finding.status not in ("fail", "warn"):
            continue
        weight = WEIGHTS.get(finding.severity, 0)
        score -= weight if finding.status == "fail" else max(1, weight // 2)
    return max(0, min(100, score))


def _counts(findings: list[Finding]) -> dict[str, int]:
    counts = {key: 0 for key in SEVERITIES}
    counts.update({key: 0 for key in STATUSES})
    counts["total"] = len(findings)
    for finding in findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
        if finding.status in ("fail", "warn"):
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def baseline_path() -> Path:
    return data_dir() / BASELINE_NAME


def save_baseline(report: dict[str, Any]) -> dict[str, Any]:
    """Guarda un baseline explícito (única escritura del auditor)."""
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "score": int(report.get("score", 0)),
        "counts": report.get("counts", {}),
        "findings": [
            {
                "id": item.get("id"),
                "category": item.get("category"),
                "title": item.get("title"),
                "severity": item.get("severity"),
                "status": item.get("status"),
            }
            for item in report.get("findings", [])
            if item.get("id")
        ],
    }
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {
        "saved_at": payload["saved_at"],
        "score": payload["score"],
        "findings": len(payload["findings"]),
    }


def load_baseline() -> dict[str, Any] | None:
    path = baseline_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return None
    return data


def delete_baseline() -> bool:
    path = baseline_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def compare(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compara el informe actual con el baseline (nuevos, resueltos, regresiones)."""
    if not baseline:
        return None
    current_map = {str(f.get("id")): f for f in current.get("findings", []) if f.get("id")}
    baseline_map = {str(f.get("id")): f for f in baseline.get("findings", []) if f.get("id")}
    new = [current_map[i] for i in current_map if i not in baseline_map]
    resolved = [baseline_map[i] for i in baseline_map if i not in current_map]
    new_actionable = [f for f in new if f.get("status") in ("fail", "warn")]
    regressed = [
        current_map[i]
        for i in current_map
        if i in baseline_map
        and baseline_map[i].get("status") == "pass"
        and current_map[i].get("status") in ("fail", "warn")
    ]
    fixed = [
        baseline_map[i]
        for i in baseline_map
        if i in current_map
        and baseline_map[i].get("status") in ("fail", "warn")
        and current_map[i].get("status") == "pass"
    ]
    still_failing = [
        current_map[i]
        for i in current_map
        if i in baseline_map
        and baseline_map[i].get("status") in ("fail", "warn")
        and current_map[i].get("status") in ("fail", "warn")
    ]
    return {
        "baseline_at": baseline.get("saved_at"),
        "baseline_score": int(baseline.get("score", 0)),
        "score_delta": int(current.get("score", 0)) - int(baseline.get("score", 0)),
        "new": new,
        "new_actionable": new_actionable,
        "resolved": resolved,
        "regressed": regressed,
        "fixed": fixed,
        "still_failing": still_failing,
    }


def run_audit(force: bool = False) -> dict[str, Any]:
    """Ejecuta la auditoría (con caché de 5 min) y devuelve el informe."""
    with _LOCK:
        now = time.monotonic()
        cached = _CACHE.get("data")
        if not force and cached is not None and now - float(_CACHE.get("at") or 0) < CACHE_TTL_SECONDS:
            data = dict(cached)
            data["cached"] = True
            return data

        started = time.perf_counter()
        findings: list[Finding] = []
        for check in _checks():
            try:
                result = check()
            except Exception as exc:  # noqa: BLE001 - un check no debe tumbar el informe
                logger.warning("Check de auditoría {} falló: {}", check.__name__, exc)
                findings.append(_unknown(
                    check.__name__.upper(), "Sistema", check.__name__,
                    f"Error inesperado: {exc}",
                ))
                continue
            if isinstance(result, Finding):
                findings.append(result)
            else:
                findings.extend(result)

        findings.sort(
            key=lambda item: (
                SEVERITIES.index(item.severity) if item.severity in SEVERITIES else 99,
                STATUSES.index(item.status) if item.status in STATUSES else 99,
                item.id,
            )
        )
        data = {
            "read_only": READ_ONLY,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "score": _score(findings),
            "counts": _counts(findings),
            "findings": [asdict(item) for item in findings],
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "cached": False,
        }
        _CACHE["at"] = now
        _CACHE["data"] = data
        return data


__all__ = [
    "Finding",
    "baseline_path",
    "compare",
    "delete_baseline",
    "load_baseline",
    "run_audit",
    "save_baseline",
    "READ_ONLY",
]
