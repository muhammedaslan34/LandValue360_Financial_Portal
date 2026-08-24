"""Verified backup and restore support for LandValue360 Platform.

The archive is self-describing and contains SHA-256 hashes for every payload.
SQLite uses the online backup API. PostgreSQL uses pg_dump/pg_restore when the
client utilities are installed in the deployment environment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any
import zipfile

from . import __version__
from .config import Settings

BACKUP_FORMAT_VERSION = "1.0"


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, stdin=subprocess.DEVNULL)
    if completed.returncode != 0:
        raise BackupError(f"External backup command failed ({completed.returncode}): {' '.join(command)}")


def create_backup(settings: Settings, output: Path, *, include_evidence: bool = True) -> dict[str, Any]:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lv360-backup-") as tmp_raw:
        tmp = Path(tmp_raw)
        payload = tmp / "payload"
        payload.mkdir()
        database_type: str
        if settings.database_url.startswith("sqlite"):
            source = settings.database_file
            if source is None or not source.exists():
                raise BackupError("SQLite database file does not exist or is in-memory.")
            destination = payload / "database.sqlite3"
            with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(destination)) as target_db:
                source_db.backup(target_db)
                target_db.execute("PRAGMA integrity_check")
            database_type = "sqlite"
        elif settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            destination = payload / "database.pgcustom"
            pg_dump = shutil.which("pg_dump")
            if not pg_dump:
                raise BackupError("pg_dump is required for PostgreSQL backups.")
            _run([pg_dump, "--format=custom", "--file", str(destination), settings.database_url.replace("+psycopg", "")])
            database_type = "postgresql"
        else:
            raise BackupError("Unsupported database URL for backup.")

        evidence_root = settings.evidence_storage_path
        if include_evidence and evidence_root.exists():
            evidence_target = payload / "evidence"
            shutil.copytree(evidence_root, evidence_target, dirs_exist_ok=True)

        files: list[dict[str, Any]] = []
        for path in sorted(p for p in payload.rglob("*") if p.is_file()):
            relative = path.relative_to(tmp).as_posix()
            files.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
        manifest = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "application_version": __version__,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_type": database_type,
            "database_url_redacted": settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url.split("///")[-1],
            "include_evidence": include_evidence,
            "files": files,
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(manifest_path, "manifest.json")
            for row in files:
                archive.write(tmp / row["path"], row["path"])
    return {**manifest, "archive": str(output), "archive_sha256": _sha256(output)}


def verify_backup(archive_path: Path) -> dict[str, Any]:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.is_file():
        raise BackupError("Backup archive does not exist.")
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        unsafe = [name for name in names if not _safe_member(name)]
        if unsafe:
            raise BackupError(f"Backup contains unsafe path(s): {', '.join(unsafe[:5])}")
        if "manifest.json" not in names:
            raise BackupError("Backup manifest is missing.")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except Exception as exc:
            raise BackupError("Backup manifest is invalid JSON.") from exc
        if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
            raise BackupError("Unsupported backup format version.")
        expected_paths = {row["path"] for row in manifest.get("files") or []}
        if not expected_paths:
            raise BackupError("Backup contains no payload files.")
        if not expected_paths.issubset(set(names)):
            raise BackupError("Backup payload is incomplete.")
        verified: list[dict[str, Any]] = []
        for row in manifest["files"]:
            path = row["path"]
            data = archive.read(path)
            actual = hashlib.sha256(data).hexdigest()
            if actual != row["sha256"]:
                raise BackupError(f"Checksum mismatch for {path}.")
            if len(data) != int(row["size"]):
                raise BackupError(f"Size mismatch for {path}.")
            verified.append({"path": path, "sha256": actual, "size": len(data)})
    return {
        "status": "VERIFIED",
        "archive": str(archive_path),
        "archive_sha256": _sha256(archive_path),
        "database_type": manifest["database_type"],
        "file_count": len(verified),
        "manifest": manifest,
    }


def restore_backup(settings: Settings, archive_path: Path, *, force: bool = False) -> dict[str, Any]:
    if not force:
        raise BackupError("Restore is destructive and requires force=True / --force.")
    verification = verify_backup(archive_path)
    manifest = verification["manifest"]
    with tempfile.TemporaryDirectory(prefix="lv360-restore-") as tmp_raw:
        tmp = Path(tmp_raw)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.infolist():
                if not _safe_member(member.filename):
                    raise BackupError(f"Unsafe archive member: {member.filename}")
                destination = tmp / member.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not member.is_dir():
                    destination.write_bytes(archive.read(member.filename))

        database_type = manifest["database_type"]
        pre_restore: str | None = None
        if database_type == "sqlite":
            target = settings.database_file
            if target is None:
                raise BackupError("SQLite restore requires a file-based target database.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                pre = target.with_name(f"{target.name}.pre-restore-{stamp}")
                shutil.copy2(target, pre)
                pre_restore = str(pre)
            source = tmp / "payload" / "database.sqlite3"
            if not source.exists():
                raise BackupError("SQLite payload is missing.")
            with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(target)) as target_db:
                source_db.backup(target_db)
                result = target_db.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise BackupError("Restored SQLite database failed integrity_check.")
        elif database_type == "postgresql":
            pg_restore = shutil.which("pg_restore")
            if not pg_restore:
                raise BackupError("pg_restore is required for PostgreSQL restores.")
            source = tmp / "payload" / "database.pgcustom"
            _run([pg_restore, "--clean", "--if-exists", "--no-owner", "--dbname", settings.database_url.replace("+psycopg", ""), str(source)])
        else:
            raise BackupError("Unsupported backup database type.")

        evidence_source = tmp / "payload" / "evidence"
        if evidence_source.exists():
            evidence_target = settings.evidence_storage_path
            evidence_target.parent.mkdir(parents=True, exist_ok=True)
            if evidence_target.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                evidence_pre = evidence_target.with_name(f"{evidence_target.name}.pre-restore-{stamp}")
                shutil.move(str(evidence_target), str(evidence_pre))
            shutil.copytree(evidence_source, evidence_target)
    return {
        "status": "RESTORED",
        "archive": str(Path(archive_path).resolve()),
        "database_type": manifest["database_type"],
        "pre_restore_database": pre_restore,
        "archive_sha256": verification["archive_sha256"],
    }
