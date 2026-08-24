from __future__ import annotations
from logging.config import fileConfig
import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from landvalue360_portal.config import get_settings
from landvalue360_portal.database import Base
from landvalue360_portal import models  # noqa

config = context.config
config.set_main_option("sqlalchemy.url", os.environ.get("LV360_PORTAL_MIGRATION_DATABASE_URL") or get_settings().database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction(): context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
