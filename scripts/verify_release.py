from __future__ import annotations
import argparse
import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    checksum_file = root / "RELEASE_CHECKSUMS.sha256"
    if not checksum_file.exists():
        raise SystemExit("RELEASE_CHECKSUMS.sha256 is missing")
    failures = []
    total = 0
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, rel = line.split("  ", 1)
        path = root / rel
        total += 1
        if not path.is_file() or digest(path) != expected:
            failures.append(rel)
    print(f"Verified: {total - len(failures)} / {total}")
    if failures:
        print("Failed:")
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
