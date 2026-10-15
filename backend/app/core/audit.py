"""Registro de auditoría encadenado por hash (a prueba de manipulación).

Cada evento se añade como una línea JSON a ``$NEXUS_DATA_DIR/audit.jsonl``.
Cada entrada incluye el hash SHA-256 de la entrada anterior, de modo que
cualquier modificación, borrado o reordenación del fichero rompe la cadena y
puede detectarse con :func:`verify_chain`.

Los ``details`` se redactan automáticamente: cualquier clave que parezca un
secreto (``*_key``, ``token``, ``password``, ``secret``...) se sustituye por
``***REDACTED***`` antes de escribirse. Las claves de API nunca llegan al
fichero de auditoría.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.core.logging_conf import logger
from app.core.paths import audit_path

_GENESIS = "0" * 64
_LOCK = threading.Lock()
_SECRET_KEY_RE = re.compile(r"(?i)(key|token|secret|password|passwd|pwd)")


def _utc_iso() -> str:
    """Marca temporal ISO-8601 en UTC con milisegundos."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _redact(value: Any) -> Any:
    """Sustituye recursivamente los valores de claves sensibles."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                result[key] = "***REDACTED***"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical(record: dict[str, Any]) -> str:
    """Serialización canónica (claves ordenadas) para el cálculo del hash."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _last_state() -> tuple[int, str]:
    """Devuelve (seq, hash) de la última entrada válida del fichero."""
    path = audit_path()
    if not path.exists():
        return 0, _GENESIS
    last_line = ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last_line = line
    except OSError as exc:
        logger.warning("No se pudo leer el fichero de auditoría: {}", exc)
        return 0, _GENESIS
    if not last_line:
        return 0, _GENESIS
    try:
        record = json.loads(last_line)
        return int(record.get("seq", 0)), str(record.get("hash", _GENESIS))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Última línea de auditoría ilegible; se continúa desde la cadena local")
        return 0, _GENESIS


def audit(
    action: str,
    *,
    actor: str = "system",
    target: str = "",
    result: str = "ok",
    details: dict[str, Any] | None = None,
) -> None:
    """Añade un evento a la cadena de auditoría.

    Parámetros:
        action: identificador del evento (p. ej. ``auth.login``).
        actor: usuario que lo provoca (``system`` para eventos internos).
        target: recurso afectado (fichero, servicio, unidad...).
        result: ``ok`` | ``denied`` | ``fail`` | ``throttled``.
        details: contexto adicional; se redactan los campos sensibles.
    """
    with _LOCK:
        seq, prev_hash = _last_state()
        record: dict[str, Any] = {
            "ts": _utc_iso(),
            "seq": seq + 1,
            "actor": actor,
            "action": action,
            "target": target,
            "result": result,
            "details": _redact(details or {}),
            "prev_hash": prev_hash,
        }
        record["hash"] = hashlib.sha256(
            (prev_hash + _canonical(record)).encode("utf-8")
        ).hexdigest()
        path = audit_path()
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(_canonical(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            logger.error("No se pudo escribir el evento de auditoría '{}': {}", action, exc)


def verify_chain() -> dict[str, Any]:
    """Recorre el fichero y comprueba que la cadena de hashes es íntegra.

    Devuelve ``{"valid": bool, "events": int, "error": str | None}``.
    """
    path = audit_path()
    if not path.exists():
        return {"valid": True, "events": 0, "error": None}
    prev_hash = _GENESIS
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    return {"valid": False, "events": count, "error": f"línea {line_no} ilegible"}
                stored_hash = record.pop("hash", "")
                expected = hashlib.sha256(
                    (prev_hash + _canonical(record)).encode("utf-8")
                ).hexdigest()
                if stored_hash != expected:
                    return {
                        "valid": False,
                        "events": count,
                        "error": f"cadena rota en seq={record.get('seq')} (línea {line_no})",
                    }
                prev_hash = stored_hash
                count += 1
    except OSError as exc:
        return {"valid": False, "events": count, "error": f"no se pudo leer: {exc}"}
    return {"valid": True, "events": count, "error": None}
