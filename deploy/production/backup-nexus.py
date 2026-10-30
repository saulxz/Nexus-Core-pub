#!/usr/bin/env python3
"""Copia de seguridad consistente y cifrada de Nexus-Core.

Genera un snapshot de SQLite con la API de backup, añade el directorio de
configuración, comprueba la integridad y **cifra el archivo antes de publicarlo**
con una clave que queda fuera de ``NEXUS_DATA_DIR`` y de los backups.

Uso:
    python3 backup-nexus.py --data-dir /var/lib/nexus \
        --out /var/backups/nexus --keep 14

La clave por defecto es ``/etc/nexus/backup.key`` (0600, root). Se puede cambiar
con ``--key-file`` o ``NEXUS_BACKUP_KEY_FILE``. ``--no-encrypt`` solo debe usarse
en desarrollo.

Pensado para ejecutarse como root desde un timer de systemd (ver
``nexus-backup.timer``).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet

DEFAULT_KEY_PATH = Path("/etc/nexus/backup.key")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/var/lib/nexus")
    parser.add_argument("--out", default="/var/backups/nexus")
    parser.add_argument("--keep", type=int, default=14, help="Copias a conservar (>=1).")
    parser.add_argument(
        "--key-file",
        default=os.environ.get("NEXUS_BACKUP_KEY_FILE", str(DEFAULT_KEY_PATH)),
        help="Clave Fernet para cifrar el backup (0600, fuera de data/).",
    )
    parser.add_argument(
        "--no-encrypt",
        action="store_true",
        help="Genera un .tar.gz sin cifrar; solo para desarrollo.",
    )
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep debe ser >= 1")
    return args


def snapshot_database(source: Path, target: Path) -> None:
    """Copia consistente con la API de backup de SQLite."""
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(target))
    integrity = None
    try:
        src.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()
    finally:
        dst.close()
        src.close()
    if not integrity or integrity[0] != "ok":
        raise SystemExit(f"Integridad del snapshot: {integrity!r}")


def load_or_create_key(path: Path) -> bytes:
    """Carga la clave Fernet o la crea con permisos 0600."""
    if path.exists():
        raw = path.read_bytes().strip()
        if not raw:
            raise SystemExit(f"La clave de backup está vacía: {path}")
        return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(key)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    print(f"Clave de backup creada: {path} (0600)")
    return key


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()
    out_dir = Path(args.out).expanduser()
    database = data_dir / "nexus.db"
    key_path = Path(args.key_file).expanduser()

    if not database.is_file():
        raise SystemExit(f"No existe la base de datos: {database}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ".tar.gz" if args.no_encrypt else ".tar.gz.enc"
    archive = out_dir / f"nexus-{stamp}{suffix}"

    with tempfile.TemporaryDirectory(prefix="nexus-backup-") as tmp:
        snapshot = Path(tmp) / "nexus.db"
        raw_backup = Path(tmp) / "nexus.tar.gz"
        snapshot_database(database, snapshot)
        with tarfile.open(raw_backup, "w:gz") as tar:
            tar.add(snapshot, arcname="nexus.db")
            config_dir = data_dir / "config"
            if config_dir.is_dir():
                for item in sorted(config_dir.rglob("*")):
                    if item.is_file():
                        tar.add(item, arcname=str(Path("config") / item.relative_to(config_dir)))
        payload = raw_backup.read_bytes()
        if args.no_encrypt:
            archive.write_bytes(payload)
            print("AVISO: backup sin cifrar (--no-encrypt).")
        else:
            archive.write_bytes(Fernet(load_or_create_key(key_path)).encrypt(payload))

    os.chmod(archive, 0o600)
    backups = sorted(
        list(out_dir.glob("nexus-*.tar.gz")) + list(out_dir.glob("nexus-*.tar.gz.enc")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[args.keep :]:
        old.unlink(missing_ok=True)

    size_kb = archive.stat().st_size // 1024
    print(
        f"Backup: {archive} ({size_kb} KB), integridad OK, "
        f"{'cifrado' if not args.no_encrypt else 'SIN CIFRAR'}, "
        f"conservadas {min(len(backups), args.keep)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
