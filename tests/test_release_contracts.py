from pathlib import Path


def test_assignment_aware_rls_migration_present():
    text = Path('migrations/versions/0004_assignment_aware_rls.py').read_text(encoding='utf-8')
    assert "app.can_view_all_projects" in text
    assert "project_assignments" in text
    assert "app.user_id" in text


def test_portal_package_schema_is_shipped():
    assert Path('schemas/portal-submission-1.0.0.schema.json').is_file()


def test_windows_launcher_has_no_nested_labels():
    text = Path('START_PORTAL.bat').read_text(encoding='utf-8')
    assert ':online' not in text
    assert ':install_app' in text


def test_recursive_assignment_rls_is_repaired_by_followup_migration():
    text = Path('migrations/versions/0005_fix_project_assignments_rls.py').read_text(encoding='utf-8')
    assert 'down_revision = "0004_assignment_aware_rls"' in text
    assert "user_id::text = current_setting('app.user_id', true)" in text
    assert 'SELECT 1 FROM project_assignments' not in text


def test_docker_runtime_fixes_are_shipped():
    compose = Path('docker-compose.yml').read_text(encoding='utf-8')
    dockerfile = Path('Dockerfile').read_text(encoding='utf-8')
    assert 'command:\n      - |' in compose
    assert 'LV360_PORTAL_HEALTH_HOST: ${DOMAIN}' in compose
    assert 'scripts/healthcheck.py' in dockerfile


def test_v220_admin_governance_migration_and_contextual_help_are_shipped():
    migration = Path('migrations/versions/0007_admin_governance_and_security.py').read_text(encoding='utf-8')
    common = Path('app/landvalue360_portal/static/common.js').read_text(encoding='utf-8')
    project = Path('app/landvalue360_portal/static/project.js').read_text(encoding='utf-8')
    assert 'down_revision = "0006_standalone_financial_portal"' in migration
    assert 'must_change_password' in migration and 'password_changed_at' in migration
    assert 'LV360_GLOSSARY' in common
    for key in ('developer_irr', 'developer_npv', 'residual_land_value', 'fair_floor', 'technical_ceiling', 'bcr', 'far'):
        assert f'{key}:' in common
    assert 'if (!canAutosave())' in project
    assert 'savePromise' in project and 'editRevision' in project and 'savedRevision' in project
