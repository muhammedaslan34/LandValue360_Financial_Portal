from __future__ import annotations

import argparse
import getpass
from sqlalchemy import select

from landvalue360_portal.database import Base, engine, session_scope
from landvalue360_portal.models import MemberRole, OrganizationMember, Role, User
from landvalue360_portal.services import create_staff_user, seed_defaults


def platform_admin_exists(db) -> bool:
    return bool(db.scalar(
        select(User.id)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .join(MemberRole, MemberRole.membership_id == OrganizationMember.id)
        .join(Role, Role.id == MemberRole.role_id)
        .where(Role.code == "PLATFORM_ADMIN", User.deleted_at.is_(None))
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--name", default="Platform Administrator")
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    with session_scope() as db:
        seed_defaults(db)
        if platform_admin_exists(db):
            print("Platform administrator already exists.")
            return 0
        if args.non_interactive and not (args.email and args.password):
            print("No administrator exists. Run CREATE_ADMIN.bat or provide --email and --password.")
            return 0
        email = args.email or input("Admin email: ").strip()
        password = args.password or getpass.getpass("Admin password (minimum 10 characters): ")
        if len(password) < 10:
            raise SystemExit("Password is too short")
        user = create_staff_user(db, email=email, password=password, full_name=args.name, role_code="PLATFORM_ADMIN")
        print(f"Administrator created: {user.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
