"""Standalone monthly financial portal, policy versions and calculation provenance.

Revision ID: 0006_standalone_financial_portal
Revises: 0005_fix_project_assignments_rls
"""
from alembic import op

from landvalue360_portal.database import Base
from landvalue360_portal import models  # noqa: F401

revision = "0006_standalone_financial_portal"
down_revision = "0005_fix_project_assignments_rls"
branch_labels = None
depends_on = None

TABLES = [
    "financial_policies",
    "financial_policy_versions",
    "engine_versions",
    "calculation_runs",
    "calculation_run_results",
    "monthly_cashflow_snapshots",
    "negotiation_results",
]


def _access(project_alias: str = "p") -> str:
    return f"""
      current_setting('app.can_view_all_projects', true) = 'true'
      OR {project_alias}.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = {project_alias}.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    """


def _policy(table: str, predicate: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS lv360_{table}_isolation ON {table}")
    op.execute(f"CREATE POLICY lv360_{table}_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")


def upgrade():
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)
    if bind.dialect.name != "postgresql":
        return
    _policy(
        "calculation_runs",
        f"EXISTS (SELECT 1 FROM projects p WHERE p.id = calculation_runs.project_id AND ({_access('p')}))",
    )
    for table in ("calculation_run_results", "monthly_cashflow_snapshots", "negotiation_results"):
        _policy(
            table,
            f"""
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = {table}.calculation_run_id AND ({_access('p')})
              )
            """,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("calculation_run_results", "monthly_cashflow_snapshots", "negotiation_results", "calculation_runs"):
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
