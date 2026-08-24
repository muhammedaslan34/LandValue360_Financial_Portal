"""Administrative access governance and password-reset controls.

Revision ID: 0007_admin_governance
Revises: 0006_standalone_financial_portal
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_admin_governance"
down_revision = "0006_standalone_financial_portal"
branch_labels = None
depends_on = None


def _column_names(bind) -> set[str]:
    """Return current users-table columns for online, inspectable databases."""
    try:
        return {str(row["name"]) for row in sa.inspect(bind).get_columns("users")}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The original 0001 migration creates the current ORM metadata on a
        # fresh database. IF NOT EXISTS therefore supports both clean installs
        # and upgrades from an existing 2.1.0 database.
        op.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "must_change_password BOOLEAN NOT NULL DEFAULT false"
        )
        op.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "password_changed_at TIMESTAMP WITH TIME ZONE NULL"
        )
        return

    columns = _column_names(bind)
    if "must_change_password" not in columns:
        op.add_column(
            "users",
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "password_changed_at" not in columns:
        op.add_column(
            "users",
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_changed_at")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password")
        return

    columns = _column_names(bind)
    if "password_changed_at" in columns:
        op.drop_column("users", "password_changed_at")
    if "must_change_password" in columns:
        op.drop_column("users", "must_change_password")
