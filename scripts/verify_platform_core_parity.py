#!/usr/bin/env python3
"""Verify byte-for-byte parity of vendored Platform 2.1.1 Python core packages."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PACKAGES = (
    "landvalue360_common",
    "landvalue360_kernel",
    "landvalue360_government",
    "landvalue360_valuation",
    "landvalue360_finance",
    "landvalue360_risk",
    "landvalue360_server",
)
ALLOWED_OMITTED_PREFIXES = ("landvalue360_server/migrations/",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for package in PACKAGES:
        package_root = root / package
        if not package_root.exists():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files[path.relative_to(root).as_posix()] = path
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform-source", type=Path, required=True, help="Platform src directory")
    parser.add_argument("--vendored-source", type=Path, default=Path("app"))
    parser.add_argument("--output", type=Path, default=Path("release_artifacts/platform-core-parity.json"))
    args = parser.parse_args()

    original = tree(args.platform_source.resolve())
    vendored = tree(args.vendored_source.resolve())
    matched: list[dict[str, str]] = []
    changed: list[dict[str, str]] = []
    missing: list[str] = []
    extra: list[str] = []
    allowed_omitted: list[str] = []

    for relative, source_path in sorted(original.items()):
        target_path = vendored.get(relative)
        if target_path is None:
            if any(relative.startswith(prefix) for prefix in ALLOWED_OMITTED_PREFIXES):
                allowed_omitted.append(relative)
            else:
                missing.append(relative)
            continue
        source_hash = sha256(source_path)
        target_hash = sha256(target_path)
        row = {"path": relative, "sha256": source_hash}
        if source_hash == target_hash:
            matched.append(row)
        else:
            changed.append({"path": relative, "source_sha256": source_hash, "vendored_sha256": target_hash})

    for relative in sorted(set(vendored) - set(original)):
        extra.append(relative)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform_release": "2.1.1-portal-integration-stable",
        "vendored_packages": list(PACKAGES),
        "status": "PASS" if not missing and not changed and not extra else "FAIL",
        "summary": {
            "source_python_files": len(original),
            "vendored_python_files": len(vendored),
            "matched": len(matched),
            "allowed_omitted": len(allowed_omitted),
            "missing": len(missing),
            "changed": len(changed),
            "extra": len(extra),
        },
        "allowed_omitted_prefixes": list(ALLOWED_OMITTED_PREFIXES),
        "allowed_omitted_files": allowed_omitted,
        "missing_files": missing,
        "changed_files": changed,
        "extra_files": extra,
        "matched_files": matched,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["summary"]}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
