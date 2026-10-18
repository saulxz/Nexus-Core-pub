#!/usr/bin/env python3
"""Copia de seguridad consistente de Nexus-Core.

Genera un tar.gz con un snapshot de SQLite (API de backup, no copia de
ficheros en caliente), el directorio de configuración y comprueba la
integridad antes de publicar el archivo. Rota las copias antiguas.

Uso:
    python3 backup-nexus.py --data-dir /var/lib/nexus \
        --out /var/backups/nexus --keep 14

Solo usa la biblioteca estándar. Pensado para ejecutarse como root
desde un timer de systemd (ver nexus-backup.timer).
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/var/lib/nexus")
    parser.add_argument("--out", default="/var/backups/nexus")
    parser.add_argument("--keep", type=int, default=14, help="Copias a conservar (>=1).")
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


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()
    out_dir = Path(args.out).expanduser()
    database = data_dir / "nexus.db"

    if not database.is_file():
        raise SystemExit(f"No existe la base de datos: {database}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = out_dir / f"nexus-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="nexus-backup-") as tmp:
        snapshot = Path(tmp) / "nexus.db"
        snapshot_database(database, snapshot)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snapshot, arcname="nexus.db")
            config_dir = data_dir / "config"
            if config_dir.is_dir():
                for item in sorted(config_dir.rglob("*")):
                    if item.is_file():
                        tar.add(item, arcname=str(Path("config") / item.relative_to(config_dir)))

    os.chmod(archive, 0o600)
    backups = sorted(out_dir.glob("nexus-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[args.keep :]:
        old.unlink(missing_ok=True)

    size_kb = archive.stat().st_size // 1024
    print(f"Backup: {archive} ({size_kb} KB), integridad OK, conservadas {min(len(backups), args.keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
