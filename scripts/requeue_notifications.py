from __future__ import annotations

import sys

from sqlalchemy import text

from landvalue360_portal.database import session_scope


def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else None
    with session_scope() as db:
        if recipient:
            result = db.execute(
                text(
                    "UPDATE notification_outbox "
                    "SET status = 'PENDING', attempts = 0, last_error = NULL "
                    "WHERE recipient = :recipient"
                ),
                {"recipient": recipient},
            )
        else:
            result = db.execute(
                text(
                    "UPDATE notification_outbox "
                    "SET status = 'PENDING', attempts = 0, last_error = NULL "
                    "WHERE status IN ('FAILED', 'SENT')"
                )
            )
        print(f"Requeued rows: {result.rowcount}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
