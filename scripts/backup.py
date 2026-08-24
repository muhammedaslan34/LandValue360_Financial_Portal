from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from landvalue360_portal.config import get_settings


def sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        row = dst.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"Integrity check failed: {row}")
    finally:
        src.close(); dst.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="backups")
    parser.add_argument("--include-files", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(args.output) / stamp
    root.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        raw = settings.database_url.split("///", 1)[-1]
        sqlite_backup(Path(raw).resolve(), root / "portal.db")
    else:
        env = os.environ.copy()
        subprocess.run(["pg_dump", settings.database_url, "-Fc", "-f", str(root / "portal.dump")], check=True, env=env)
    if args.include_files and settings.storage_backend == "local" and settings.storage_path.exists():
        shutil.copytree(settings.storage_path, root / "private", dirs_exist_ok=True)
    manifest = {"created_at_utc": stamp, "database_url_kind": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql", "files_included": bool(args.include_files and settings.storage_backend == "local")}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
