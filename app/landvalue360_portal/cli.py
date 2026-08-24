from __future__ import annotations

import argparse
import getpass
from pathlib import Path

import uvicorn

from .config import get_settings
from .database import Base, engine, session_scope
from .main import create_app
from .services import create_staff_user, seed_defaults


def main() -> None:
    parser = argparse.ArgumentParser(prog="lv360-portal")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8090, type=int)
    init = sub.add_parser("init-db")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--email")
    admin.add_argument("--name", default="Platform Administrator")
    args = parser.parse_args()
    if args.command == "serve":
        uvicorn.run("landvalue360_portal.main:app", host=args.host, port=args.port, reload=False)
    elif args.command == "init-db":
        Base.metadata.create_all(engine)
        with session_scope() as db:
            seed_defaults(db)
        print("Database initialized.")
    elif args.command == "create-admin":
        email = args.email or input("Admin email: ").strip()
        password = getpass.getpass("Admin password (min 10 chars): ")
        if len(password) < 10:
            raise SystemExit("Password is too short")
        Base.metadata.create_all(engine)
        with session_scope() as db:
            seed_defaults(db)
            user = create_staff_user(db, email=email, password=password, full_name=args.name, role_code="PLATFORM_ADMIN")
        print(f"Administrator ready: {user.email}")
