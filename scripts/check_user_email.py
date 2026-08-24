from __future__ import annotations

import sys

from sqlalchemy import func, select, text

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import User


def main() -> int:
    emails = sys.argv[1:] or [
        "muhammedaslan201999@gmail.com",
        "muhammadaslan201999@gmail.com",
    ]
    with session_scope() as db:
        for email in emails:
            print(f"=== {email} ===")
            user = db.scalar(select(User).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None)))
            if not user:
                print("USER: not found")
            else:
                print(
                    "USER:",
                    {
                        "id": user.id,
                        "email": user.email,
                        "email_verified": bool(user.email_verified_at),
                        "email_verified_at": str(user.email_verified_at),
                        "active": bool(user.active),
                        "must_change_password": bool(user.must_change_password),
                        "last_login_at": str(user.last_login_at),
                    },
                )
            rows = db.execute(
                text(
                    "SELECT status, attempts, LEFT(last_error, 120), template_code, created_at "
                    "FROM notification_outbox WHERE recipient = :email ORDER BY created_at DESC"
                ),
                {"email": email},
            ).all()
            if not rows:
                print("OUTBOX: (none)")
            for row in rows:
                print("OUTBOX:", row)
            tokens = db.execute(
                text(
                    "SELECT kind, expires_at, used_at "
                    "FROM one_time_tokens t JOIN users u ON u.id = t.user_id "
                    "WHERE lower(u.email) = lower(:email) ORDER BY t.created_at DESC LIMIT 3"
                ),
                {"email": email},
            ).all()
            if not tokens:
                print("TOKENS: (none)")
            for token in tokens:
                print("TOKEN:", token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
