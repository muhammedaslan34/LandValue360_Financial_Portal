"""Remove recursive self-reference from project_assignments RLS.

Revision ID: 0005_fix_project_assignments_rls
Revises: 0004_assignment_aware_rls
"""
from alembic import op

revision = "0005_fix_project_assignments_rls"
down_revision = "0004_assignment_aware_rls"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS lv360_project_assignments_isolation ON project_assignments")
    op.execute(
        """
        CREATE POLICY lv360_project_assignments_isolation
        ON project_assignments
        USING (
            current_setting('app.can_view_all_projects', true) = 'true'
            OR user_id::text = current_setting('app.user_id', true)
        )
        WITH CHECK (
            current_setting('app.can_view_all_projects', true) = 'true'
            OR user_id::text = current_setting('app.user_id', true)
        )
        """
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS lv360_project_assignments_isolation ON project_assignments")
