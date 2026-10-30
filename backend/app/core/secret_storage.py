"""Cifrado en reposo de secretos de Nexus-Core.

La clave **no vive en la base de datos ni en el directorio de datos**: en Linux
se instala en ``/etc/nexus/secret.key`` (0600) y en Windows se guarda en
``%LOCALAPPDATA%\\Nexus-Core\\secret.key``. Así, una copia de ``nexus.db`` o de
los backups no permite descifrar el secreto TOTP por sí sola.

El formato cifrado es ``enc:v1:<token Fernet>``. Los valores sin prefijo se
aceptan como legado en claro para no romper una instalación anterior; el
siguiente ``stage_totp`` los vuelve a guardar cifrados.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.paths import data_dir

_PREFIX = "enc:v1:"
_KEY_ENV = "NEXUS_SECRET_KEY_FILE"
_key_cache: tuple[Path, bytes] | None = None


def _default_key_path() -> Path:
    """Ruta de la clave maestra fuera del directorio de datos."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "Nexus-Core" / "secret.key"
    etc = Path("/etc/nexus/secret.key")
    if etc.exists() or os.access(etc.parent, os.W_OK):
        return etc
    # Desarrollo o instalaciones sin /etc/nexus: no hay otro sitio portable.
    return data_dir() / "secret.key"


def key_path() -> Path:
    """Ruta efectiva de la clave maestra (``NEXUS_SECRET_KEY_FILE`` manda)."""
    override = os.environ.get(_KEY_ENV, "").strip()
    return Path(override) if override else _default_key_path()


def _load_key() -> bytes:
    """Lee la clave, creándola con permisos 0600 si no existe."""
    global _key_cache
    path = key_path()
    if _key_cache and _key_cache[0] == path:
        return _key_cache[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_bytes().strip()
        if not raw:
            raise RuntimeError(f"La clave de secretos está vacía: {path}")
    else:
        raw = Fernet.generate_key()
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(raw)
        try:
            os.chmod(temporary, 0o600)
        except OSError:  # Windows: los permisos POSIX no aplican.
            pass
        os.replace(temporary, path)
    _key_cache = (path, raw)
    return raw


def encrypt(value: str) -> str:
    """Cifra un texto y devuelve ``enc:v1:...``."""
    token = Fernet(_load_key()).encrypt(str(value).encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Descifra un valor; acepta texto plano legado sin prefijo."""
    text = str(value or "")
    if not text.startswith(_PREFIX):
        return text
    try:
        return Fernet(_load_key()).decrypt(text[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("No se pudo descifrar el secreto: clave incorrecta o dato corrupto.") from exc


def is_encrypted(value: str) -> bool:
    """True si el valor usa el formato cifrado actual."""
    return str(value or "").startswith(_PREFIX)


__all__ = ["decrypt", "encrypt", "is_encrypted", "key_path"]
