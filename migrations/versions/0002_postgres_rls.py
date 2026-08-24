"""PostgreSQL RLS policies for tenant and project isolation."""
from alembic import op
revision = "0002_postgres_rls"
down_revision = "0001_initial_portal"
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
VERSION_CHILDREN = ["land_inputs", "planning_inputs", "land_use_allocations", "product_allocations", "cost_items", "calculation_checks", "project_declarations"]

def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql": return
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS lv360_projects_isolation ON projects")
    op.execute("""CREATE POLICY lv360_projects_isolation ON projects USING (
        current_setting('app.is_staff', true) = 'true'
        OR organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
    ) WITH CHECK (
        current_setting('app.is_staff', true) = 'true'
        OR organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
    )""")
    for table, project_col in PROJECT_CHILDREN.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS lv360_{table}_isolation ON {table}")
        op.execute(f"""CREATE POLICY lv360_{table}_isolation ON {table} USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = {table}.{project_col}
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = {table}.{project_col}
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        )""")
    for table in VERSION_CHILDREN:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS lv360_{table}_isolation ON {table}")
        op.execute(f"""CREATE POLICY lv360_{table}_isolation ON {table} USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = {table}.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = {table}.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        )""")

def downgrade():
    bind=op.get_bind()
    if bind.dialect.name != "postgresql": return
    for table in ["projects", *PROJECT_CHILDREN.keys(), *VERSION_CHILDREN]:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
