"""Paquete de diagnóstico descargable (ZIP) con secretos redactados.

Un solo ``GET`` descarga un ZIP con todo lo necesario para documentar una
incidencia o supportar el proyecto:

* ``manifest.json`` — versiones, host y entorno **saneado**; marca el paquete
  como sensible.
* ``app.log`` — últimas líneas del log de la aplicación.
* ``journal.log`` — últimas líneas del journal del sistema (si hay systemd).
* ``config/*`` — ficheros de configuración gestionados por el panel.
* ``metrics.csv`` — 24 h de métricas.
* ``audit.jsonl`` — cola de la auditoría.
* ``state.json`` — monitores, reglas de alerta y canales de notificación.

**Todo pasa por :func:`redact_text` / :func:`redact_structure` antes de
escribirse**: claves de IA, contraseñas SMTP, tokens de Telegram, secretos de
sesión y hashes de tokens de API se sustituyen por ``***REDACTED***``. El
paquete se genera entero en memoria (la SD de una Raspberry Pi no debería
sufrir por una descarga) y se devuelve en un único trozo.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.audit import audit, verify_chain
from app.core.config import get_settings
from app.core.logging_conf import logger
from app.core.paths import ai_env_path, audit_path
from app.modules import alerts, fim, monitors, notifications
from app.modules.config_manager import TARGETS
from app.modules.metrics_store import export_rows
from app.modules.network_config import NETPLAN_DROPIN
from app.modules.system_tools import run_capture

#: Fichero de log de la aplicación dentro del despliegue en contenedor.
APP_LOG_PATH = "/app/data/nexus.log"

#: Líneas de log incluidas por fichero de texto.
LOG_TAIL_LINES = 400

#: Variables de entorno que se copian al manifiesto (el resto, fuera).
ENV_ALLOWLIST: tuple[str, ...] = (
    "NEXUS_DATA_DIR",
    "AI_PROVIDER",
    "AI_MODEL",
    "SESSION_IDLE_MINUTES",
    "SESSION_ABSOLUTE_HOURS",
    "SESSION_COOKIE_SECURE",
    "LOGIN_RATE_LIMIT",
    "MONITORS_ENABLED",
    "ALLOWED_IMAGES",
    "CONTAINER_NAME_PREFIX",
    "DOCKER_SOCKET",
    "FRONTEND_DIR",
    "API_PREFIX",
)

#: Patrón de claves cuyo valor es un secreto (mismo criterio que la auditoría).
_SECRET_KEY_RE = re.compile(r"(?i)(key|token|secret|password|passwd|pwd|credential|bot)")

#: Valores con forma de secreto que pueden aparecer *sueltos* en un texto.
_SECRET_VALUE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),                    # OpenAI
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{12,}"),                # Anthropic
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),                   # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),                # GitHub
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}"),              # Telegram (bot token)
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),             # cabecera Authorization
    re.compile(r"\bnx_[A-Za-z0-9_\-]{16,}"),                    # token de API de Nexus
    re.compile(r"\b[A-Fa-f0-9]{64}\b"),                         # hash sha256 (tokens, sesiones)
)

#: Marcador que sustituye a cualquier secreto.
REDACTED = "***REDACTED***"


# ============================ Redacción =================================
def redact_structure(value: Any) -> Any:
    """Sustituye recursivamente los valores de las claves sensibles."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)) and not isinstance(item, (dict, list)):
                result[key] = REDACTED if item not in (None, "", 0, False) else item
            else:
                result[key] = redact_structure(item)
        return result
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    return value


def redact_text(text: str) -> str:
    """Redacta secretos en un texto libre (líneas de log, ficheros de config).

    Cubre dos casos:

    * ``CLAVE=valor`` / ``clave: valor`` donde la clave parece un secreto.
    * Valores sueltos con forma conocida (claves de API, tokens de bot, hashes).
    """
    if not text:
        return text
    lines: list[str] = []
    for raw in text.splitlines():
        match = re.match(r"^(\s*[A-Za-z0-9_.\-]+\s*[=:]\s*)(.*)$", raw)
        if match and _SECRET_KEY_RE.search(match.group(1)):
            value = match.group(2)
            lines.append(f"{match.group(1)}{REDACTED}" if value.strip() else raw)
            continue
        cleaned = raw
        for pattern in _SECRET_VALUE_RES:
            cleaned = pattern.sub(REDACTED, cleaned)
        lines.append(cleaned)
    return "\n".join(lines)


def sanitized_env() -> dict[str, str]:
    """Variables de entorno relevantes, sin ningún valor de secreto."""
    return {key: os.environ[key] for key in ENV_ALLOWLIST if os.environ.get(key)}


# ============================ Recogida =================================
def _tail_file(path: str, lines: int = LOG_TAIL_LINES) -> tuple[str, str | None]:
    """Últimas líneas de un fichero de texto (``("", motivo)`` si no se puede)."""
    if not os.path.isfile(path):
        return ("", f"no existe: {path}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError as exc:
        return ("", f"no se pudo leer {path}: {exc}")
    return ("".join(content[-lines:]), None)


def _journal_tail(lines: int = LOG_TAIL_LINES) -> tuple[str, str | None]:
    """Últimas líneas del journal del sistema (si hay journalctl y permisos)."""
    if shutil.which("journalctl") is None:
        return ("", "journalctl no está disponible en este sistema")
    code, output = run_capture(["journalctl", "-n", str(lines), "--no-pager"], timeout=15.0)
    if code != 0:
        return ("", f"journalctl devolvió el código {code}: {output.strip()[-160:]}")
    return (output, None)


def _managed_config() -> dict[str, str]:
    """Ficheros de configuración que gestiona el panel (``etiqueta -> texto``)."""
    files: dict[str, str] = {}
    for label, path in (
        ("samba.conf", TARGETS["samba"]),
        ("dnsmasq-nexus.conf", TARGETS["dnsmasq"]),
        ("netplan-90-nexus.yaml", NETPLAN_DROPIN),
        ("ai.env", str(ai_env_path())),
    ):
        text, reason = _tail_file(path, lines=10_000)
        files[label] = text if text else f"# {reason}\n"
    return files


def _metrics_csv(hours: int = 24) -> str:
    """Métricas de las últimas ``hours`` horas en CSV (mismo formato que la web)."""
    import csv

    _period, header, rows = export_rows("24h" if hours <= 24 else "7d")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _state_dump() -> dict[str, Any]:
    """Volcado JSON del estado del panel: monitores, reglas, canales, FIM."""
    channels = notifications.list_channels()  # ya viene con los secretos enmascarados
    return {
        "monitors": monitors.list_summaries(),
        "alert_rules": alerts.list_rules(),
        "alert_events": alerts.list_events(limit=50),
        "notification_channels": channels,
        "fim": fim.status(),
        "schema_version": db.SCHEMA_VERSION,
        "audit_chain": verify_chain(),
    }


def build_manifest(entries: list[str]) -> dict[str, Any]:
    """Manifiesto del paquete: qué contiene, de dónde salió y de qué es sensible."""
    settings = get_settings()
    return {
        "sensitive": True,
        "warning": (
            "Este paquete contiene información interna del servidor. Se han "
            "redactado las claves de API, contraseñas, tokens y hashes, pero "
            "trátalo como confidencial."
        ),
        "redacted_patterns": [
            "claves de IA (OpenAI/Anthropic/Gemini)",
            "contraseñas SMTP y tokens de Telegram/webhook",
            "tokens de API de Nexus (nx_...)",
            "hashes SHA-256 (secretos de sesión y de tokens)",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": {"name": settings.APP_NAME, "version": settings.APP_VERSION},
        "schema_version": db.SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "host": {
            "hostname": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "docker_socket": settings.DOCKER_SOCKET,
        },
        "env": sanitized_env(),
        "files": entries,
        "omitted": [],
    }


# ============================ Construcción ==============================
def build_bundle() -> bytes:
    """Construye el ZIP de diagnóstico completo y lo devuelve en memoria."""
    settings = get_settings()
    buffer = io.BytesIO()
    omitted: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        def add(name: str, text: str) -> None:
            """Escribe un miembro del ZIP con el texto ya redactado."""
            archive.writestr(name, redact_text(text))

        log_text, log_reason = _tail_file(APP_LOG_PATH)
        if log_text:
            add("app.log", log_text)
        else:
            add("app.log", f"# {log_reason}\n")
            omitted.append("app.log")

        journal_text, journal_reason = _journal_tail()
        if journal_text:
            add("journal.log", journal_text)
        else:
            add("journal.log", f"# {journal_reason}\n")
            omitted.append("journal.log")

        for name, text in _managed_config().items():
            add(f"config/{name}", text)

        add("metrics.csv", _metrics_csv())

        audit_text, audit_reason = _tail_file(str(audit_path()), lines=LOG_TAIL_LINES * 2)
        if audit_text:
            add("audit.jsonl", audit_text)
        else:
            add("audit.jsonl", f"# {audit_reason}\n")
            omitted.append("audit.jsonl")

        try:
            state = json.dumps(_state_dump(), ensure_ascii=False, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001 - el paquete no debe fallar por esto
            logger.warning("No se pudo volcar el estado en el diagnóstico: {}", exc)
            state = json.dumps({"error": str(exc)})
            omitted.append("state.json")
        add("state.json", state)

        manifest = build_manifest(
            [
                "app.log",
                "journal.log",
                "config/samba.conf",
                "config/dnsmasq-nexus.conf",
                "config/netplan-90-nexus.yaml",
                "config/ai.env",
                "metrics.csv",
                "audit.jsonl",
                "state.json",
            ]
        )
        manifest["omitted"] = omitted
        archive.writestr(
            "manifest.json",
            json.dumps(redact_structure(manifest), ensure_ascii=False, indent=2, default=str),
        )

    logger.info(
        "Paquete de diagnóstico generado ({} B, {} ficheros, {} omitidos).",
        settings.APP_NAME,
        buffer.tell(),
        len(omitted),
    )
    return buffer.getvalue()


def record_download(actor: str, size_bytes: int) -> None:
    """Anota en la auditoría la descarga del paquete y su tamaño."""
    audit(
        "system.diagnostics.downloaded",
        actor=actor,
        target="diagnostics.zip",
        details={"sensitivo": True, "tamano_bytes": int(size_bytes)},
    )


__all__ = [
    "APP_LOG_PATH",
    "ENV_ALLOWLIST",
    "LOG_TAIL_LINES",
    "REDACTED",
    "build_bundle",
    "build_manifest",
    "redact_structure",
    "redact_text",
    "record_download",
    "sanitized_env",
]