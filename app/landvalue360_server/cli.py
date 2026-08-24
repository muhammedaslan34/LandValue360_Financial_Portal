"""Administrative command line for development and deployment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .backup import create_backup, restore_backup, verify_backup
from .config import Settings
from .database import Database
from .services.auth import bootstrap_development
from .web_defaults import ensure_development_policy, ensure_valuation_policy


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _alembic_config(settings: Settings):  # noqa: ANN201
    try:
        from alembic.config import Config
    except ImportError as exc:
        raise RuntimeError("Alembic is required. Install the 'api' dependency extra.") from exc

    repository_config = _root() / "alembic.ini"
    config = Config(str(repository_config)) if repository_config.exists() else Config()
    migration_path = Path(__file__).resolve().parent / "migrations"
    if not migration_path.exists():
        raise RuntimeError(f"Packaged migrations not found at {migration_path}")
    config.set_main_option("script_location", str(migration_path))
    config.set_main_option("prepend_sys_path", str(_root() / "src"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def _alembic_upgrade(settings: Settings, revision: str = "head") -> None:
    try:
        from alembic import command
    except ImportError as exc:
        raise RuntimeError("Alembic is required. Install the 'api' dependency extra.") from exc
    command.upgrade(_alembic_config(settings), revision)


def cmd_migrate(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _alembic_upgrade(settings, args.revision)
    print(f"Database migrated to {args.revision}.")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    database = Database(settings)
    if args.create_schema:
        database.create_schema()
    with database.session() as session:
        organization, workspace, user, membership = bootstrap_development(
            session,
            settings=settings,
            email=args.email,
            password=args.password,
        )
        policy_pack, policy_version = ensure_development_policy(
            session,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        valuation_pack, valuation_version = ensure_valuation_policy(
            session,
            organization=organization,
            workspace=workspace,
            user=user,
        )
    print("Bootstrap completed.")
    print(f"Organization: {organization.slug}")
    print(f"Workspace: {workspace.slug}")
    print(f"Administrator: {user.email}")
    print(f"Membership role: {membership.role}")
    print(f"Published project policy: {policy_pack.code} / {policy_version.version_label}")
    print(f"Published valuation policy: {valuation_pack.code} / {valuation_version.version_label}")
    return 0


def cmd_export_openapi(args: argparse.Namespace) -> int:
    from .main import create_app

    settings = Settings(
        environment="test",
        edition_mode=args.edition,
        database_url="sqlite+pysqlite:///:memory:",
        password_iterations=10_000,
        auto_create_schema=False,
        enable_docs=True,
    )
    app = create_app(settings=settings, database=Database(settings))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OpenAPI exported to {output}")
    return 0



def cmd_backup(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    result = create_backup(settings, Path(args.output), include_evidence=not args.no_evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_verify_backup(args: argparse.Namespace) -> int:
    result = verify_backup(Path(args.archive))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    result = restore_backup(settings, Path(args.archive), force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Uvicorn is required. Install the 'api' dependency extra.") from exc
    if args.edition:
        os.environ["LV360_EDITION_MODE"] = args.edition
    settings = Settings.from_env()
    uvicorn.run(
        "landvalue360_server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        access_log=bool(args.access_log or settings.enable_access_log),
        proxy_headers=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LandValue360 Enterprise API administration")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="Apply Alembic database migrations")
    migrate.add_argument("--revision", default="head")
    migrate.set_defaults(handler=cmd_migrate)

    bootstrap = sub.add_parser("bootstrap", help="Create the first local organization and administrator")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--password", required=True)
    bootstrap.add_argument(
        "--create-schema",
        action="store_true",
        help="Use SQLAlchemy create_all instead of Alembic (development only)",
    )
    bootstrap.set_defaults(handler=cmd_bootstrap)

    export_openapi = sub.add_parser("export-openapi", help="Export the OpenAPI contract")
    export_openapi.add_argument("--output", default="openapi/landvalue360-api-v2.1.1.json")
    export_openapi.add_argument("--edition", choices=("combined", "developer", "government", "administration"), default="combined")
    export_openapi.set_defaults(handler=cmd_export_openapi)


    backup = sub.add_parser("backup", help="Create a verified database and evidence backup")
    backup.add_argument("--output", required=True)
    backup.add_argument("--no-evidence", action="store_true")
    backup.set_defaults(handler=cmd_backup)

    verify_backup_parser = sub.add_parser("verify-backup", help="Verify a backup archive and all checksums")
    verify_backup_parser.add_argument("archive")
    verify_backup_parser.set_defaults(handler=cmd_verify_backup)

    restore = sub.add_parser("restore", help="Restore a verified backup archive")
    restore.add_argument("archive")
    restore.add_argument("--force", action="store_true", help="Confirm destructive restore")
    restore.set_defaults(handler=cmd_restore)

    serve = sub.add_parser("serve", help="Run the FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--log-level", default="info")
    serve.add_argument("--edition", choices=("combined", "developer", "government", "administration"), default=None)
    serve.add_argument("--access-log", action="store_true", help="Enable HTTP access logging; disabled by default to minimize sensitive metadata exposure")
    serve.set_defaults(handler=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
