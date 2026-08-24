from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    configured = os.getenv("LV360_PORTAL_HEALTH_HOST") or os.getenv("LV360_PORTAL_TRUSTED_HOSTS") or "127.0.0.1"
    host = configured.split(",", 1)[0].strip() or "127.0.0.1"
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
