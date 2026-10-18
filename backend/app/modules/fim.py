"""Integridad de ficheros de configuración (FIM-lite) con líneas base.

Vigila **únicamente** los ficheros que el panel ya gestiona: una lista blanca
fija (``WATCHED_FILES``) construida con las rutas de los módulos existentes. No
se acepta ninguna ruta del cliente en el FIM: compararlo con la lista blanca es
la única forma de que el endpoint no se convierta en un lector de ficheros
arbitrario del servidor (ni de ``/etc/shadow``).

Estados por fichero:

* ``sin_baseline`` — vigilado pero todavía sin línea base (el primer
  ``POST /api/fim/baseline`` la crea).
* ``ok`` — el contenido coincide con la línea base.
* ``modificado`` — difiere: se devuelve el diff unificado con ``difflib``.
* ``ausente`` — estaba en la línea base pero ya no existe en disco.

La línea base guarda el SHA-256 (detección rápida) **y** el contenido (para el
diff). Ambos viven en la base de datos local del panel, junto al resto de estado
privado de la instalación.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core import db
from app.core.audit import audit
from app.core.logging_conf import logger
from app.core.paths import ai_env_path
from app.modules.config_manager import TARGETS
from app.modules.network_config import NETPLAN_DROPIN

#: Máximo de líneas del diff que se devuelven por fichero.
MAX_DIFF_LINES = 400

#: Máximo de caracteres del diff por fichero (protege el navegador y la SD).
MAX_DIFF_CHARS = 16_384

#: Ficheros vigilados: ``(etiqueta, ruta)``. La lista es cerrada y no se amplía
#: desde fuera; cada entrada procede de las rutas que el panel ya controla.
WATCHED_FILES: tuple[tuple[str, str], ...] = (
    ("samba", TARGETS["samba"]),
    ("dnsmasq", TARGETS["dnsmasq"]),
    ("netplan", NETPLAN_DROPIN),
    ("ai_env", str(ai_env_path())),
)


def _now_iso() -> str:
    """Marca temporal actual en ISO-8601 UTC (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def watched_paths() -> tuple[str, ...]:
    """Rutas vigiladas por el FIM (constante, en el orden de la lista blanca)."""
    return tuple(path for _label, path in WATCHED_FILES)


def _label_for(path: str) -> str:
    """Etiqueta lógica de una ruta vigilada (``''`` si no está vigilada)."""
    for label, watched in WATCHED_FILES:
        if watched == path:
            return label
    return ""


def _sha256(text: str) -> str:
    """SHA-256 hexadecimal de un texto."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _read(path: str) -> str | None:
    """Lee un fichero como texto (``None`` si no existe o no se puede leer)."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError, OSError):
        return None


def _baseline_rows() -> dict[str, dict[str, Any]]:
    """Todas las líneas base guardadas, indexadas por ruta."""
    conn = db.connect()
    try:
        rows = conn.execute("SELECT path, sha256, content, updated_at FROM config_baselines").fetchall()
    finally:
        conn.close()
    return {str(row["path"]): db.row_to_dict(row) for row in rows}


def _unified_diff(baseline: str, current: str, label: str) -> tuple[str, int, int, bool]:
    """Diff unificado entre la línea base y el contenido actual.

    Returns:
        ``(diff, líneas_borradas, líneas_añadidas, recortado)``.
    """
    base_lines = baseline.splitlines()
    current_lines = current.splitlines()
    removed = added = 0
    chunks: list[str] = []
    for line in difflib.unified_diff(
        base_lines, current_lines, fromfile=f"{label} (línea base)", tofile=f"{label} (actual)", lineterm=""
    ):
        if line.startswith("-") and not line.startswith("---"):
            removed += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        chunks.append(line)

    truncated = False
    if len(chunks) > MAX_DIFF_LINES:
        chunks = chunks[:MAX_DIFF_LINES]
        truncated = True
    text = "\n".join(chunks)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
        truncated = True
    return text, removed, added, truncated


def file_status(path: str, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Estado de un fichero vigilado (el FIM nunca acepta rutas arbitrarias).

    Args:
        path: Ruta vigilada (debe estar en :data:`watched_paths`).
        baseline: Línea base ya cargada; si es ``None`` se consulta.
    """
    label = _label_for(path) or Path(path).name
    current = _read(path)
    if baseline is None:
        baseline = _baseline_rows().get(path)

    status: dict[str, Any] = {
        "path": path,
        "label": label,
        "exists": current is not None,
        "baseline_sha256": (baseline or {}).get("sha256"),
        "baseline_updated_at": (baseline or {}).get("updated_at"),
        "current_sha256": _sha256(current) if current is not None else None,
        "state": "sin_baseline",
        "diff": "",
        "missing_lines": 0,
        "extra_lines": 0,
        "diff_truncated": False,
    }

    if baseline is None:
        return status
    if current is None:
        status["state"] = "ausente"
        return status

    stored_hash = str(baseline["sha256"])
    if _sha256(current) == stored_hash:
        status["state"] = "ok"
        return status

    status["state"] = "modificado"
    diff, removed, added, truncated = _unified_diff(
        str(baseline.get("content") or ""), current, label
    )
    status.update(
        {"diff": diff, "missing_lines": removed, "extra_lines": added, "diff_truncated": truncated}
    )
    return status


def status() -> dict[str, Any]:
    """Estado del FIM para todos los ficheros vigilados."""
    baselines = _baseline_rows()
    files = [file_status(path, baselines.get(path)) for path in watched_paths()]
    changed = sum(1 for item in files if item["state"] in ("modificado", "ausente"))
    return {"count": len(files), "changed": changed, "files": files}


def _snapshot(path: str) -> bool:
    """Guarda (o replaces) la línea base de un fichero vigilado.

    Returns:
        ``True`` si se guardó; ``False`` si el fichero no existe en disco.
    """
    content = _read(path)
    if content is None:
        logger.info("FIM: no se crea línea base de '{}' porque no existe.", path)
        return False
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO config_baselines (path, sha256, content, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
            "sha256 = excluded.sha256, content = excluded.content, "
            "updated_at = excluded.updated_at",
            (path, _sha256(content), content, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def snapshot(paths: list[str] | None = None, *, actor: str = "system") -> dict[str, Any]:
    """Actualiza las líneas base del FIM y lo deja en la auditoría.

    Args:
        paths: Subconjunto de rutas vigiladas. Cualquier ruta fuera de la lista
            blanca se **ignora** (no se lee ni se escribe nada de ella).
        actor: Usuario que lo ordena (para la auditoría).

    Returns:
        ``{"count": int, "paths": [rutas actualizadas]}``.
    """
    allowed = watched_paths()
    requested = allowed if not paths else [item for item in paths if item in allowed]
    updated = [path for path in requested if _snapshot(path)]
    audit(
        "fim.baseline.updated",
        actor=actor,
        target=",".join(updated) or "-",
        details={"ficheros": len(updated), "rutas": updated},
    )
    return {"count": len(updated), "paths": updated}


__all__ = [
    "MAX_DIFF_CHARS",
    "MAX_DIFF_LINES",
    "WATCHED_FILES",
    "file_status",
    "snapshot",
    "status",
    "watched_paths",
]