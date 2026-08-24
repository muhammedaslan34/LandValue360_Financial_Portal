"""Make project RLS assignment-aware instead of granting all staff global access."""
from alembic import op

revision = "0004_assignment_aware_rls"
down_revision = "0003_extended_project_rls"
branch_labels = None
depends_on = None

PROJECT_CHILDREN = {
    "project_versions": "project_id",
    "project_status_history": "project_id",
    "project_assignments": "project_id",
    "project_documents": "project_id",
    "information_requests": "project_id",
    "analysis_exports": "project_id",
    "analysis_imports": "project_id",
    "reports": "project_id",
}
VERSION_CHILDREN = [
    "land_inputs", "planning_inputs", "land_use_allocations", "product_allocations",
    "cost_items", "calculation_checks", "project_declarations",
]
INDIRECT = {
    "product_pricing": """
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id AND ({access})
      )
    """,
    "information_request_messages": """
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id AND ({access})
      )
    """,
    "report_versions": """
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id AND ({access})
      )
    """,
    "report_downloads": """
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id AND ({access})
      )
    """,
}


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


def _replace_policy(table: str, predicate: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS lv360_{table}_isolation ON {table}")
    op.execute(f"CREATE POLICY lv360_{table}_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _replace_policy("projects", _access("projects"))
    for table, project_col in PROJECT_CHILDREN.items():
        predicate = f"EXISTS (SELECT 1 FROM projects p WHERE p.id = {table}.{project_col} AND ({_access('p')}))"
        _replace_policy(table, predicate)
    for table in VERSION_CHILDREN:
        predicate = f"""
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = {table}.project_version_id AND ({_access('p')})
          )
        """
        _replace_policy(table, predicate)
    for table, template in INDIRECT.items():
        _replace_policy(table, template.format(access=_access('p')))


def downgrade():
    # Reapply the preceding broad staff policy by delegating to a conservative
    # organization-only predicate. Downgrades are for emergency rollback only.
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    predicate = "current_setting('app.can_view_all_projects', true) = 'true' OR organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))"
    _replace_policy("projects", predicate)
