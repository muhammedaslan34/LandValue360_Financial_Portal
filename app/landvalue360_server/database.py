"""Database lifecycle and request-scoped session utilities."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings
from .models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        database_file = settings.database_file
        if database_file is not None:
            database_file.parent.mkdir(parents=True, exist_ok=True)

        connect_args: dict[str, object] = {}
        engine_kwargs: dict[str, object] = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            connect_args["timeout"] = 30.0
            if settings.database_url.endswith(":memory:"):
                engine_kwargs["poolclass"] = StaticPool

        self.engine: Engine = create_engine(
            settings.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
            **engine_kwargs,
        )
        if settings.database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
            future=True,
        )

    @staticmethod
    def _configure_sqlite(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            # In-memory and read-only SQLite connections may reject WAL.
            pass
        cursor.close()

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_schema(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def apply_postgres_tenant_context(session: Session, organization_id: str | None) -> None:
    """Set the PostgreSQL tenant context used by optional row-level security.

    Repository queries remain explicitly tenant-scoped. RLS is a second line of
    defense for deployments that use a non-owner database role.
    """

    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    value = organization_id or ""
    session.execute(
        text("SELECT set_config('app.current_organization_id', :organization_id, true)"),
        {"organization_id": value},
    )
