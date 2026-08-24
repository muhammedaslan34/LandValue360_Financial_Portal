#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "RELEASE_CHECKSUMS.sha256"
SKIP_PARTS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
    ".sample-runtime", ".release-staging", "build", "data",
}
SKIP_FILES = {OUT.name}


def excluded(relative: Path) -> bool:
    if any(part in SKIP_PARTS for part in relative.parts):
        return True
    if relative.parts[:2] == ("tests", ".runtime"):
        return True
    return relative.name in SKIP_FILES


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    rows = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if excluded(relative):
            continue
        rows.append(f"{digest(path)}  {relative.as_posix()}")
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(rows)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
