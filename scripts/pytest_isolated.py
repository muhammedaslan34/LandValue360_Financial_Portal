#!/usr/bin/env python3
"""Run one pytest target and terminate deterministically after pytest finishes.

Some optional instrumentation/plugins or background libraries can leave worker
threads alive during sequential release-gate subprocesses. The tests have
already completed at that point, so the release gate uses this wrapper to flush
results and exit with pytest's exact status without waiting on unrelated
interpreter shutdown hooks.
"""
from __future__ import annotations

import os
import sys
import pytest


def main() -> None:
    code = int(pytest.main(sys.argv[1:]))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)


if __name__ == "__main__":
    main()
