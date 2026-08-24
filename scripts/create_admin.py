from __future__ import annotations
from landvalue360_portal.cli import main

if __name__ == "__main__":
    import sys
    sys.argv = [sys.argv[0], "create-admin", *sys.argv[1:]]
    main()
