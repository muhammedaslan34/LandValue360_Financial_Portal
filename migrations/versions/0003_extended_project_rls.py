"""Extend PostgreSQL RLS to indirect project children."""
from alembic import op

revision = "0003_extended_project_rls"
down_revision = "0002_postgres_rls"
branch_labels = None
depends_on = None

POLICIES = {
    "product_pricing": """
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    """,
    "information_request_messages": """
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    """,
    "report_versions": """
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    """,
    "report_downloads": """
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    """,
}


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, predicate in POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS lv360_{table}_isolation ON {table}")
        op.execute(f"CREATE POLICY lv360_{table}_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in POLICIES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
