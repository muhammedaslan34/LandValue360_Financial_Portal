from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    # Internal container health checks must not depend on public DOMAIN substitution.
    host = "127.0.0.1"
    request = urllib.request.Request(
        "http://127.0.0.1:8090/api/health/ready",
        headers={"Host": host},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
