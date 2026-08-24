from __future__ import annotations

from sqlalchemy import text

from landvalue360_portal.database import session_scope


def main() -> int:
    with session_scope() as db:
        rows = db.execute(
            text(
                "SELECT status, attempts, LEFT(last_error, 120), recipient, template_code, created_at "
                "FROM notification_outbox ORDER BY created_at DESC LIMIT 10"
            )
        ).all()
        for row in rows:
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
