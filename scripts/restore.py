from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from landvalue360_portal.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    backup = Path(args.backup).resolve()
    if not args.yes:
        answer = input(f"Restore from {backup}? Type RESTORE: ")
        if answer != "RESTORE":
            raise SystemExit("Cancelled")
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        raw = settings.database_url.split("///", 1)[-1]
        target = Path(raw).resolve()
        source = backup / "portal.db" if backup.is_dir() else backup
        check = sqlite3.connect(source)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Backup database failed integrity check")
        finally:
            check.close()
        if target.exists():
            safety = target.with_suffix(target.suffix + ".pre-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            shutil.copy2(target, safety)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if backup.is_dir() and (backup / "private").exists() and settings.storage_backend == "local":
            shutil.copytree(backup / "private", settings.storage_path, dirs_exist_ok=True)
    else:
        dump = backup / "portal.dump" if backup.is_dir() else backup
        subprocess.run(["pg_restore", "--clean", "--if-exists", "-d", settings.database_url, str(dump)], check=True, env=os.environ.copy())
    print("Restore completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
