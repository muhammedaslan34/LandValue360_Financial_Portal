BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_initial_portal

CREATE TABLE users (
    id VARCHAR(36) NOT NULL, 
    email VARCHAR(320) NOT NULL, 
    password_hash VARCHAR(512) NOT NULL, 
    full_name VARCHAR(200) NOT NULL, 
    email_verified_at TIMESTAMP WITH TIME ZONE, 
    active BOOLEAN NOT NULL, 
    suspended BOOLEAN NOT NULL, 
    failed_login_count INTEGER NOT NULL, 
    locked_until TIMESTAMP WITH TIME ZONE, 
    last_login_at TIMESTAMP WITH TIME ZONE, 
    must_change_password BOOLEAN NOT NULL, 
    password_changed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE organizations (
    id VARCHAR(36) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    slug VARCHAR(120) NOT NULL, 
    kind VARCHAR(40) NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_organizations_slug ON organizations (slug);

CREATE TABLE roles (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(60) NOT NULL, 
    name_ar VARCHAR(160) NOT NULL, 
    name_en VARCHAR(160) NOT NULL, 
    system_role BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE permissions (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    description VARCHAR(300) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE notification_outbox (
    id VARCHAR(36) NOT NULL, 
    recipient VARCHAR(320) NOT NULL, 
    template_code VARCHAR(80) NOT NULL, 
    payload JSON NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    attempts INTEGER NOT NULL, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id)
);

CREATE TABLE login_attempts (
    id VARCHAR(36) NOT NULL, 
    identifier VARCHAR(320) NOT NULL, 
    ip_address VARCHAR(80), 
    success BOOLEAN NOT NULL, 
    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_login_attempts_ip_address ON login_attempts (ip_address);

CREATE INDEX ix_login_attempts_attempted_at ON login_attempts (attempted_at);

CREATE INDEX ix_login_attempts_identifier ON login_attempts (identifier);

CREATE TABLE system_settings (
    id VARCHAR(36) NOT NULL, 
    key VARCHAR(120) NOT NULL, 
    value JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (key)
);

CREATE TABLE email_templates (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(80) NOT NULL, 
    subject_ar VARCHAR(250) NOT NULL, 
    subject_en VARCHAR(250) NOT NULL, 
    body_ar TEXT NOT NULL, 
    body_en TEXT NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE file_type_policies (
    id VARCHAR(36) NOT NULL, 
    extension VARCHAR(20) NOT NULL, 
    mime_types JSON NOT NULL, 
    max_size_bytes INTEGER NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (extension)
);

CREATE TABLE financial_policies (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    name VARCHAR(240) NOT NULL, 
    description TEXT, 
    active BOOLEAN NOT NULL, 
    current_version_id VARCHAR(36), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_financial_policies_code ON financial_policies (code);

CREATE TABLE engine_versions (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    engine_version VARCHAR(40) NOT NULL, 
    adapter_version VARCHAR(40) NOT NULL, 
    source_hash VARCHAR(64) NOT NULL, 
    manifest JSON NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_engine_version_release UNIQUE (code, engine_version, adapter_version, source_hash)
);

CREATE INDEX ix_engine_versions_code ON engine_versions (code);

CREATE INDEX ix_engine_versions_active ON engine_versions (active);

CREATE TABLE profiles (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    phone VARCHAR(80), 
    country VARCHAR(120), 
    preferred_language VARCHAR(8) NOT NULL, 
    applicant_type VARCHAR(40) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (user_id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE organization_members (
    id VARCHAR(36) NOT NULL, 
    organization_id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    is_owner BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_org_member UNIQUE (organization_id, user_id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_organization_members_organization_id ON organization_members (organization_id);

CREATE INDEX ix_organization_members_user_id ON organization_members (user_id);

CREATE TABLE role_permissions (
    id VARCHAR(36) NOT NULL, 
    role_id VARCHAR(36) NOT NULL, 
    permission_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_role_permission UNIQUE (role_id, permission_id), 
    FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE, 
    FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE
);

CREATE TABLE access_sessions (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    token_hash VARCHAR(128) NOT NULL, 
    csrf_token VARCHAR(128) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    ip_address VARCHAR(80), 
    user_agent VARCHAR(500), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    UNIQUE (token_hash)
);

CREATE INDEX ix_access_sessions_user_id ON access_sessions (user_id);

CREATE TABLE one_time_tokens (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    kind VARCHAR(40) NOT NULL, 
    token_hash VARCHAR(128) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    used_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    UNIQUE (token_hash)
);

CREATE TABLE projects (
    id VARCHAR(36) NOT NULL, 
    organization_id VARCHAR(36) NOT NULL, 
    owner_user_id VARCHAR(36) NOT NULL, 
    reference VARCHAR(80) NOT NULL, 
    name VARCHAR(240) NOT NULL, 
    description TEXT, 
    status VARCHAR(40) NOT NULL, 
    priority VARCHAR(20) NOT NULL, 
    current_version_id VARCHAR(36), 
    submitted_at TIMESTAMP WITH TIME ZONE, 
    ready_for_analysis_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    sla_due_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_project_reference UNIQUE (organization_id, reference), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id), 
    FOREIGN KEY(owner_user_id) REFERENCES users (id)
);

CREATE INDEX ix_projects_owner_user_id ON projects (owner_user_id);

CREATE INDEX ix_projects_status ON projects (status);

CREATE INDEX ix_project_status_org ON projects (organization_id, status);

CREATE INDEX ix_projects_organization_id ON projects (organization_id);

CREATE TABLE notifications (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    kind VARCHAR(60) NOT NULL, 
    title VARCHAR(250) NOT NULL, 
    body TEXT NOT NULL, 
    link VARCHAR(500), 
    read_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE TABLE privacy_requests (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    request_type VARCHAR(40) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    notes TEXT, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_privacy_requests_user_id ON privacy_requests (user_id);

CREATE TABLE user_consents (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    consent_type VARCHAR(80) NOT NULL, 
    text_version VARCHAR(30) NOT NULL, 
    accepted BOOLEAN NOT NULL, 
    ip_address VARCHAR(80), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE financial_policy_versions (
    id VARCHAR(36) NOT NULL, 
    financial_policy_id VARCHAR(36) NOT NULL, 
    version_number INTEGER NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    immutable BOOLEAN NOT NULL, 
    change_reason TEXT, 
    policy_snapshot JSON NOT NULL, 
    snapshot_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_financial_policy_version UNIQUE (financial_policy_id, version_number), 
    FOREIGN KEY(financial_policy_id) REFERENCES financial_policies (id) ON DELETE CASCADE
);

CREATE INDEX ix_financial_policy_versions_status ON financial_policy_versions (status);

CREATE INDEX ix_financial_policy_versions_financial_policy_id ON financial_policy_versions (financial_policy_id);

CREATE INDEX ix_financial_policy_versions_snapshot_hash ON financial_policy_versions (snapshot_hash);

CREATE TABLE member_roles (
    id VARCHAR(36) NOT NULL, 
    membership_id VARCHAR(36) NOT NULL, 
    role_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_member_role UNIQUE (membership_id, role_id), 
    FOREIGN KEY(membership_id) REFERENCES organization_members (id) ON DELETE CASCADE, 
    FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
);

CREATE TABLE project_versions (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    version_number INTEGER NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    immutable BOOLEAN NOT NULL, 
    change_reason TEXT, 
    submitted_at TIMESTAMP WITH TIME ZONE, 
    snapshot_hash VARCHAR(128), 
    completeness_percent NUMERIC(12, 6) NOT NULL, 
    input_snapshot JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_project_version UNIQUE (project_id, version_number), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX ix_version_project_status ON project_versions (project_id, status);

CREATE INDEX ix_project_versions_project_id ON project_versions (project_id);

CREATE TABLE project_status_history (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    from_status VARCHAR(40), 
    to_status VARCHAR(40) NOT NULL, 
    reason TEXT, 
    changed_by VARCHAR(36) NOT NULL, 
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(changed_by) REFERENCES users (id)
);

CREATE INDEX ix_project_status_history_project_id ON project_status_history (project_id);

CREATE TABLE project_assignments (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    assignment_type VARCHAR(30) NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_project_assignment_type UNIQUE (project_id, assignment_type), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_project_assignments_user_id ON project_assignments (user_id);

CREATE INDEX ix_project_assignments_project_id ON project_assignments (project_id);

CREATE TABLE audit_logs (
    id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36), 
    organization_id VARCHAR(36), 
    project_id VARCHAR(36), 
    action VARCHAR(120) NOT NULL, 
    entity_type VARCHAR(80) NOT NULL, 
    entity_id VARCHAR(36), 
    before_data JSON, 
    after_data JSON, 
    ip_address VARCHAR(80), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE INDEX ix_audit_logs_project_id ON audit_logs (project_id);

CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id);

CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE land_inputs (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    gross_land_area_sqm NUMERIC(24, 6) NOT NULL, 
    excluded_land_area_sqm NUMERIC(24, 6) NOT NULL, 
    title_reference VARCHAR(250), 
    location VARCHAR(300), 
    current_land_value NUMERIC(24, 6), 
    currency VARCHAR(8) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (project_version_id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE TABLE planning_inputs (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    far NUMERIC(12, 6) NOT NULL, 
    bcr NUMERIC(12, 6), 
    planning_status VARCHAR(200), 
    project_duration_months INTEGER, 
    sales_duration_months INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (project_version_id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE TABLE land_use_allocations (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    code VARCHAR(60) NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    percentage NUMERIC(12, 6) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_land_use_allocations_project_version_id ON land_use_allocations (project_version_id);

CREATE TABLE product_allocations (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    code VARCHAR(60) NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    allocation_percentage NUMERIC(12, 6) NOT NULL, 
    sellable_efficiency_percentage NUMERIC(12, 6) NOT NULL, 
    unit_selling_price NUMERIC(24, 6) NOT NULL, 
    currency VARCHAR(8) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_product_allocations_project_version_id ON product_allocations (project_version_id);

CREATE TABLE cost_items (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    category VARCHAR(80) NOT NULL, 
    amount NUMERIC(24, 6), 
    currency VARCHAR(8) NOT NULL, 
    quantity_basis VARCHAR(80), 
    quantity NUMERIC(24, 6), 
    unit_cost NUMERIC(24, 6), 
    developer_share_percentage NUMERIC(12, 6) NOT NULL, 
    net_sales_deductible BOOLEAN NOT NULL, 
    notes TEXT, 
    source VARCHAR(250), 
    evidence_confidence VARCHAR(30), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_cost_items_project_version_id ON cost_items (project_version_id);

CREATE TABLE project_documents (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36), 
    owner_user_id VARCHAR(36) NOT NULL, 
    category VARCHAR(80) NOT NULL, 
    original_name VARCHAR(300) NOT NULL, 
    stored_name VARCHAR(160) NOT NULL, 
    storage_key VARCHAR(500) NOT NULL, 
    mime_type VARCHAR(160) NOT NULL, 
    size_bytes INTEGER NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    scan_status VARCHAR(30) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE SET NULL, 
    FOREIGN KEY(owner_user_id) REFERENCES users (id), 
    UNIQUE (storage_key)
);

CREATE INDEX ix_project_documents_project_version_id ON project_documents (project_version_id);

CREATE INDEX ix_project_documents_project_id ON project_documents (project_id);

CREATE TABLE information_requests (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    requested_by VARCHAR(36) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    subject VARCHAR(250) NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    closed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id), 
    FOREIGN KEY(requested_by) REFERENCES users (id)
);

CREATE INDEX ix_information_requests_project_id ON information_requests (project_id);

CREATE TABLE analysis_exports (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    export_type VARCHAR(40) NOT NULL, 
    package_version VARCHAR(30) NOT NULL, 
    checksum VARCHAR(64) NOT NULL, 
    storage_key VARCHAR(500), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id)
);

CREATE TABLE analysis_imports (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    imported_by VARCHAR(36) NOT NULL, 
    source_reference VARCHAR(250), 
    calculation_run_reference VARCHAR(250), 
    status VARCHAR(30) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id), 
    FOREIGN KEY(imported_by) REFERENCES users (id)
);

CREATE TABLE reports (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    report_type VARCHAR(60) NOT NULL, 
    language VARCHAR(8) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    current_version_id VARCHAR(36), 
    published_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id)
);

CREATE INDEX ix_report_project_status ON reports (project_id, status);

CREATE INDEX ix_reports_project_id ON reports (project_id);

CREATE TABLE project_declarations (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    declaration_code VARCHAR(80) NOT NULL, 
    accepted BOOLEAN NOT NULL, 
    text_version VARCHAR(30) NOT NULL, 
    accepted_by VARCHAR(36), 
    accepted_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE, 
    FOREIGN KEY(accepted_by) REFERENCES users (id)
);

CREATE TABLE calculation_checks (
    id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    actual_value VARCHAR(250), 
    required_value VARCHAR(250), 
    message_ar VARCHAR(500) NOT NULL, 
    message_en VARCHAR(500) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_calculation_checks_project_version_id ON calculation_checks (project_version_id);

CREATE TABLE calculation_runs (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    financial_policy_version_id VARCHAR(36) NOT NULL, 
    engine_version_id VARCHAR(36) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    run_type VARCHAR(30) NOT NULL, 
    currency VARCHAR(8) NOT NULL, 
    selected_contract_method VARCHAR(40), 
    input_snapshot JSON NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    result_hash VARCHAR(64), 
    executed_by VARCHAR(36) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    duration_ms INTEGER, 
    error_message TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(financial_policy_version_id) REFERENCES financial_policy_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(engine_version_id) REFERENCES engine_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(executed_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_calculation_runs_input_hash ON calculation_runs (input_hash);

CREATE INDEX ix_calculation_runs_executed_by ON calculation_runs (executed_by);

CREATE INDEX ix_calculation_runs_project_version_id ON calculation_runs (project_version_id);

CREATE INDEX ix_calculation_runs_result_hash ON calculation_runs (result_hash);

CREATE INDEX ix_calculation_runs_status ON calculation_runs (status);

CREATE INDEX ix_calculation_runs_engine_version_id ON calculation_runs (engine_version_id);

CREATE INDEX ix_calculation_runs_financial_policy_version_id ON calculation_runs (financial_policy_version_id);

CREATE INDEX ix_calculation_run_project_created ON calculation_runs (project_id, created_at);

CREATE INDEX ix_calculation_runs_project_id ON calculation_runs (project_id);

CREATE INDEX ix_calculation_run_inputs ON calculation_runs (project_version_id, financial_policy_version_id, engine_version_id, input_hash);

CREATE TABLE product_pricing (
    id VARCHAR(36) NOT NULL, 
    product_allocation_id VARCHAR(36) NOT NULL, 
    price_source VARCHAR(250), 
    evidence_confidence VARCHAR(30), 
    notes TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    UNIQUE (product_allocation_id), 
    FOREIGN KEY(product_allocation_id) REFERENCES product_allocations (id) ON DELETE CASCADE
);

CREATE TABLE information_request_messages (
    id VARCHAR(36) NOT NULL, 
    request_id VARCHAR(36) NOT NULL, 
    author_user_id VARCHAR(36) NOT NULL, 
    body TEXT NOT NULL, 
    internal_only BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(request_id) REFERENCES information_requests (id) ON DELETE CASCADE, 
    FOREIGN KEY(author_user_id) REFERENCES users (id)
);

CREATE INDEX ix_information_request_messages_request_id ON information_request_messages (request_id);

CREATE TABLE report_versions (
    id VARCHAR(36) NOT NULL, 
    report_id VARCHAR(36) NOT NULL, 
    version_number INTEGER NOT NULL, 
    uploaded_by VARCHAR(36) NOT NULL, 
    approved_by VARCHAR(36), 
    approved_at TIMESTAMP WITH TIME ZONE, 
    storage_key VARCHAR(500) NOT NULL, 
    original_name VARCHAR(300) NOT NULL, 
    mime_type VARCHAR(160) NOT NULL, 
    size_bytes INTEGER NOT NULL, 
    checksum VARCHAR(64) NOT NULL, 
    calculation_run_reference VARCHAR(250), 
    status VARCHAR(30) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_report_version UNIQUE (report_id, version_number), 
    FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE, 
    FOREIGN KEY(uploaded_by) REFERENCES users (id), 
    FOREIGN KEY(approved_by) REFERENCES users (id)
);

CREATE TABLE calculation_run_results (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    calculation_status VARCHAR(30) NOT NULL, 
    policy_compliant BOOLEAN NOT NULL, 
    reconciliation_passed BOOLEAN NOT NULL, 
    summary JSON NOT NULL, 
    financial_truth JSON NOT NULL, 
    residual_valuation JSON NOT NULL, 
    annual_cashflow JSON NOT NULL, 
    selected_contract JSON NOT NULL, 
    constraints JSON NOT NULL, 
    full_result JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_calculation_run_results_calculation_run_id ON calculation_run_results (calculation_run_id);

CREATE INDEX ix_calculation_run_results_calculation_status ON calculation_run_results (calculation_status);

CREATE TABLE monthly_cashflow_snapshots (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    month_number INTEGER NOT NULL, 
    cashflow_date TIMESTAMP WITH TIME ZONE, 
    opening_cash NUMERIC(24, 6) NOT NULL, 
    gross_contracted_sales NUMERIC(24, 6) NOT NULL, 
    gross_collections NUMERIC(24, 6) NOT NULL, 
    net_collections NUMERIC(24, 6) NOT NULL, 
    planned_cost NUMERIC(24, 6) NOT NULL, 
    actual_cost NUMERIC(24, 6) NOT NULL, 
    deferred_cost NUMERIC(24, 6) NOT NULL, 
    equity_contribution NUMERIC(24, 6) NOT NULL, 
    financing_draw NUMERIC(24, 6) NOT NULL, 
    interest_paid NUMERIC(24, 6) NOT NULL, 
    financing_fees NUMERIC(24, 6) NOT NULL, 
    financing_repayment NUMERIC(24, 6) NOT NULL, 
    landowner_payment NUMERIC(24, 6) NOT NULL, 
    developer_distribution NUMERIC(24, 6) NOT NULL, 
    ending_cash NUMERIC(24, 6) NOT NULL, 
    ending_debt NUMERIC(24, 6) NOT NULL, 
    funding_gap NUMERIC(24, 6) NOT NULL, 
    contractual_arrears NUMERIC(24, 6) NOT NULL, 
    cash_balance_variance NUMERIC(24, 6) NOT NULL, 
    data JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_run_month_snapshot UNIQUE (calculation_run_id, month_number), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_monthly_cashflow_snapshots_calculation_run_id ON monthly_cashflow_snapshots (calculation_run_id);

CREATE INDEX ix_monthly_cashflow_snapshots_cashflow_date ON monthly_cashflow_snapshots (cashflow_date);

CREATE INDEX ix_monthly_cashflow_run_date ON monthly_cashflow_snapshots (calculation_run_id, cashflow_date);

CREATE TABLE negotiation_results (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    method VARCHAR(40) NOT NULL, 
    status VARCHAR(80) NOT NULL, 
    measure_type VARCHAR(20) NOT NULL, 
    fair_floor NUMERIC(24, 6), 
    balanced NUMERIC(24, 6), 
    technical_ceiling NUMERIC(24, 6), 
    negotiation_minimum NUMERIC(24, 6), 
    negotiation_maximum NUMERIC(24, 6), 
    governing_constraint_id VARCHAR(120), 
    recommendation_rank INTEGER, 
    result_snapshot JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_run_negotiation_method UNIQUE (calculation_run_id, method), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_negotiation_results_method ON negotiation_results (method);

CREATE INDEX ix_negotiation_results_calculation_run_id ON negotiation_results (calculation_run_id);

CREATE TABLE report_downloads (
    id VARCHAR(36) NOT NULL, 
    report_version_id VARCHAR(36) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    downloaded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    ip_address VARCHAR(80), 
    PRIMARY KEY (id), 
    FOREIGN KEY(report_version_id) REFERENCES report_versions (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id)
);

INSERT INTO alembic_version (version_num) VALUES ('0001_initial_portal') RETURNING alembic_version.version_num;

-- Running upgrade 0001_initial_portal -> 0002_postgres_rls

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_projects_isolation ON projects;

CREATE POLICY lv360_projects_isolation ON projects USING (
        current_setting('app.is_staff', true) = 'true'
        OR organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
    ) WITH CHECK (
        current_setting('app.is_staff', true) = 'true'
        OR organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
    );

ALTER TABLE project_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_versions_isolation ON project_versions;

CREATE POLICY lv360_project_versions_isolation ON project_versions USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_versions.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_versions.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE project_status_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_status_history_isolation ON project_status_history;

CREATE POLICY lv360_project_status_history_isolation ON project_status_history USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_status_history.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_status_history.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE project_assignments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_assignments_isolation ON project_assignments;

CREATE POLICY lv360_project_assignments_isolation ON project_assignments USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_assignments.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_assignments.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE project_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_documents_isolation ON project_documents;

CREATE POLICY lv360_project_documents_isolation ON project_documents USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_documents.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = project_documents.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE information_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_information_requests_isolation ON information_requests;

CREATE POLICY lv360_information_requests_isolation ON information_requests USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = information_requests.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = information_requests.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE analysis_exports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_analysis_exports_isolation ON analysis_exports;

CREATE POLICY lv360_analysis_exports_isolation ON analysis_exports USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = analysis_exports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = analysis_exports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE analysis_imports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_analysis_imports_isolation ON analysis_imports;

CREATE POLICY lv360_analysis_imports_isolation ON analysis_imports USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = analysis_imports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = analysis_imports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_reports_isolation ON reports;

CREATE POLICY lv360_reports_isolation ON reports USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = reports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM projects p WHERE p.id = reports.project_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE land_inputs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_land_inputs_isolation ON land_inputs;

CREATE POLICY lv360_land_inputs_isolation ON land_inputs USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = land_inputs.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = land_inputs.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE planning_inputs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_planning_inputs_isolation ON planning_inputs;

CREATE POLICY lv360_planning_inputs_isolation ON planning_inputs USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = planning_inputs.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = planning_inputs.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE land_use_allocations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_land_use_allocations_isolation ON land_use_allocations;

CREATE POLICY lv360_land_use_allocations_isolation ON land_use_allocations USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = land_use_allocations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = land_use_allocations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE product_allocations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_product_allocations_isolation ON product_allocations;

CREATE POLICY lv360_product_allocations_isolation ON product_allocations USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = product_allocations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = product_allocations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE cost_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_cost_items_isolation ON cost_items;

CREATE POLICY lv360_cost_items_isolation ON cost_items USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = cost_items.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = cost_items.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE calculation_checks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_calculation_checks_isolation ON calculation_checks;

CREATE POLICY lv360_calculation_checks_isolation ON calculation_checks USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = calculation_checks.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = calculation_checks.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

ALTER TABLE project_declarations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_declarations_isolation ON project_declarations;

CREATE POLICY lv360_project_declarations_isolation ON project_declarations USING (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = project_declarations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        ) WITH CHECK (
          current_setting('app.is_staff', true) = 'true' OR EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id=v.project_id
            WHERE v.id = project_declarations.project_version_id
              AND p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
          )
        );

UPDATE alembic_version SET version_num='0002_postgres_rls' WHERE alembic_version.version_num = '0001_initial_portal';

-- Running upgrade 0002_postgres_rls -> 0003_extended_project_rls

ALTER TABLE product_pricing ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_product_pricing_isolation ON product_pricing;

CREATE POLICY lv360_product_pricing_isolation ON product_pricing USING (
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    );

ALTER TABLE information_request_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_information_request_messages_isolation ON information_request_messages;

CREATE POLICY lv360_information_request_messages_isolation ON information_request_messages USING (
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    );

ALTER TABLE report_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_report_versions_isolation ON report_versions;

CREATE POLICY lv360_report_versions_isolation ON report_versions USING (
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    );

ALTER TABLE report_downloads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_report_downloads_isolation ON report_downloads;

CREATE POLICY lv360_report_downloads_isolation ON report_downloads USING (
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id
          AND (current_setting('app.is_staff', true) = 'true'
               OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ',')))
      )
    );

UPDATE alembic_version SET version_num='0003_extended_project_rls' WHERE alembic_version.version_num = '0002_postgres_rls';

-- Running upgrade 0003_extended_project_rls -> 0004_assignment_aware_rls

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_projects_isolation ON projects;

CREATE POLICY lv360_projects_isolation ON projects USING (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR projects.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = projects.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ) WITH CHECK (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR projects.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = projects.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    );

ALTER TABLE project_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_versions_isolation ON project_versions;

CREATE POLICY lv360_project_versions_isolation ON project_versions USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_versions.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_versions.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE project_status_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_status_history_isolation ON project_status_history;

CREATE POLICY lv360_project_status_history_isolation ON project_status_history USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_status_history.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_status_history.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE project_assignments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_assignments_isolation ON project_assignments;

CREATE POLICY lv360_project_assignments_isolation ON project_assignments USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_assignments.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_assignments.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE project_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_documents_isolation ON project_documents;

CREATE POLICY lv360_project_documents_isolation ON project_documents USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_documents.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = project_documents.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE information_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_information_requests_isolation ON information_requests;

CREATE POLICY lv360_information_requests_isolation ON information_requests USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = information_requests.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = information_requests.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE analysis_exports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_analysis_exports_isolation ON analysis_exports;

CREATE POLICY lv360_analysis_exports_isolation ON analysis_exports USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = analysis_exports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = analysis_exports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE analysis_imports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_analysis_imports_isolation ON analysis_imports;

CREATE POLICY lv360_analysis_imports_isolation ON analysis_imports USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = analysis_imports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = analysis_imports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_reports_isolation ON reports;

CREATE POLICY lv360_reports_isolation ON reports USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = reports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = reports.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE land_inputs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_land_inputs_isolation ON land_inputs;

CREATE POLICY lv360_land_inputs_isolation ON land_inputs USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = land_inputs.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = land_inputs.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE planning_inputs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_planning_inputs_isolation ON planning_inputs;

CREATE POLICY lv360_planning_inputs_isolation ON planning_inputs USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = planning_inputs.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = planning_inputs.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE land_use_allocations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_land_use_allocations_isolation ON land_use_allocations;

CREATE POLICY lv360_land_use_allocations_isolation ON land_use_allocations USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = land_use_allocations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = land_use_allocations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE product_allocations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_product_allocations_isolation ON product_allocations;

CREATE POLICY lv360_product_allocations_isolation ON product_allocations USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = product_allocations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = product_allocations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE cost_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_cost_items_isolation ON cost_items;

CREATE POLICY lv360_cost_items_isolation ON cost_items USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = cost_items.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = cost_items.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE calculation_checks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_calculation_checks_isolation ON calculation_checks;

CREATE POLICY lv360_calculation_checks_isolation ON calculation_checks USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = calculation_checks.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = calculation_checks.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE project_declarations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_project_declarations_isolation ON project_declarations;

CREATE POLICY lv360_project_declarations_isolation ON project_declarations USING (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = project_declarations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        ) WITH CHECK (
          EXISTS (
            SELECT 1 FROM project_versions v JOIN projects p ON p.id = v.project_id
            WHERE v.id = project_declarations.project_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
          )
        );

ALTER TABLE product_pricing ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_product_pricing_isolation ON product_pricing;

CREATE POLICY lv360_product_pricing_isolation ON product_pricing USING (
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM product_allocations pa
        JOIN project_versions v ON v.id = pa.project_version_id
        JOIN projects p ON p.id = v.project_id
        WHERE pa.id = product_pricing.product_allocation_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    );

ALTER TABLE information_request_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_information_request_messages_isolation ON information_request_messages;

CREATE POLICY lv360_information_request_messages_isolation ON information_request_messages USING (
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM information_requests ir
        JOIN projects p ON p.id = ir.project_id
        WHERE ir.id = information_request_messages.request_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    );

ALTER TABLE report_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_report_versions_isolation ON report_versions;

CREATE POLICY lv360_report_versions_isolation ON report_versions USING (
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.id = report_versions.report_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    );

ALTER TABLE report_downloads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_report_downloads_isolation ON report_downloads;

CREATE POLICY lv360_report_downloads_isolation ON report_downloads USING (
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    ) WITH CHECK (
      EXISTS (
        SELECT 1 FROM report_versions rv
        JOIN reports r ON r.id = rv.report_id
        JOIN projects p ON p.id = r.project_id
        WHERE rv.id = report_downloads.report_version_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
      )
    );

UPDATE alembic_version SET version_num='0004_assignment_aware_rls' WHERE alembic_version.version_num = '0003_extended_project_rls';

-- Running upgrade 0004_assignment_aware_rls -> 0005_fix_project_assignments_rls

DROP POLICY IF EXISTS lv360_project_assignments_isolation ON project_assignments;

CREATE POLICY lv360_project_assignments_isolation
        ON project_assignments
        USING (
            current_setting('app.can_view_all_projects', true) = 'true'
            OR user_id::text = current_setting('app.user_id', true)
        )
        WITH CHECK (
            current_setting('app.can_view_all_projects', true) = 'true'
            OR user_id::text = current_setting('app.user_id', true)
        );

UPDATE alembic_version SET version_num='0005_fix_project_assignments_rls' WHERE alembic_version.version_num = '0004_assignment_aware_rls';

-- Running upgrade 0005_fix_project_assignments_rls -> 0006_standalone_financial_portal

CREATE TABLE financial_policies (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    name VARCHAR(240) NOT NULL, 
    description TEXT, 
    active BOOLEAN NOT NULL, 
    current_version_id VARCHAR(36), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_financial_policies_code ON financial_policies (code);

CREATE TABLE financial_policy_versions (
    id VARCHAR(36) NOT NULL, 
    financial_policy_id VARCHAR(36) NOT NULL, 
    version_number INTEGER NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    immutable BOOLEAN NOT NULL, 
    change_reason TEXT, 
    policy_snapshot JSON NOT NULL, 
    snapshot_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_financial_policy_version UNIQUE (financial_policy_id, version_number), 
    FOREIGN KEY(financial_policy_id) REFERENCES financial_policies (id) ON DELETE CASCADE
);

CREATE INDEX ix_financial_policy_versions_status ON financial_policy_versions (status);

CREATE INDEX ix_financial_policy_versions_financial_policy_id ON financial_policy_versions (financial_policy_id);

CREATE INDEX ix_financial_policy_versions_snapshot_hash ON financial_policy_versions (snapshot_hash);

CREATE TABLE engine_versions (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(100) NOT NULL, 
    engine_version VARCHAR(40) NOT NULL, 
    adapter_version VARCHAR(40) NOT NULL, 
    source_hash VARCHAR(64) NOT NULL, 
    manifest JSON NOT NULL, 
    active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_engine_version_release UNIQUE (code, engine_version, adapter_version, source_hash)
);

CREATE INDEX ix_engine_versions_code ON engine_versions (code);

CREATE INDEX ix_engine_versions_active ON engine_versions (active);

CREATE TABLE calculation_runs (
    id VARCHAR(36) NOT NULL, 
    project_id VARCHAR(36) NOT NULL, 
    project_version_id VARCHAR(36) NOT NULL, 
    financial_policy_version_id VARCHAR(36) NOT NULL, 
    engine_version_id VARCHAR(36) NOT NULL, 
    status VARCHAR(30) NOT NULL, 
    run_type VARCHAR(30) NOT NULL, 
    currency VARCHAR(8) NOT NULL, 
    selected_contract_method VARCHAR(40), 
    input_snapshot JSON NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    result_hash VARCHAR(64), 
    executed_by VARCHAR(36) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    duration_ms INTEGER, 
    error_message TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(project_version_id) REFERENCES project_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(financial_policy_version_id) REFERENCES financial_policy_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(engine_version_id) REFERENCES engine_versions (id) ON DELETE RESTRICT, 
    FOREIGN KEY(executed_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_calculation_runs_input_hash ON calculation_runs (input_hash);

CREATE INDEX ix_calculation_runs_executed_by ON calculation_runs (executed_by);

CREATE INDEX ix_calculation_runs_project_version_id ON calculation_runs (project_version_id);

CREATE INDEX ix_calculation_runs_result_hash ON calculation_runs (result_hash);

CREATE INDEX ix_calculation_runs_status ON calculation_runs (status);

CREATE INDEX ix_calculation_runs_engine_version_id ON calculation_runs (engine_version_id);

CREATE INDEX ix_calculation_runs_financial_policy_version_id ON calculation_runs (financial_policy_version_id);

CREATE INDEX ix_calculation_run_project_created ON calculation_runs (project_id, created_at);

CREATE INDEX ix_calculation_runs_project_id ON calculation_runs (project_id);

CREATE INDEX ix_calculation_run_inputs ON calculation_runs (project_version_id, financial_policy_version_id, engine_version_id, input_hash);

CREATE TABLE calculation_run_results (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    calculation_status VARCHAR(30) NOT NULL, 
    policy_compliant BOOLEAN NOT NULL, 
    reconciliation_passed BOOLEAN NOT NULL, 
    summary JSON NOT NULL, 
    financial_truth JSON NOT NULL, 
    residual_valuation JSON NOT NULL, 
    annual_cashflow JSON NOT NULL, 
    selected_contract JSON NOT NULL, 
    constraints JSON NOT NULL, 
    full_result JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_calculation_run_results_calculation_run_id ON calculation_run_results (calculation_run_id);

CREATE INDEX ix_calculation_run_results_calculation_status ON calculation_run_results (calculation_status);

CREATE TABLE monthly_cashflow_snapshots (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    month_number INTEGER NOT NULL, 
    cashflow_date TIMESTAMP WITH TIME ZONE, 
    opening_cash NUMERIC(24, 6) NOT NULL, 
    gross_contracted_sales NUMERIC(24, 6) NOT NULL, 
    gross_collections NUMERIC(24, 6) NOT NULL, 
    net_collections NUMERIC(24, 6) NOT NULL, 
    planned_cost NUMERIC(24, 6) NOT NULL, 
    actual_cost NUMERIC(24, 6) NOT NULL, 
    deferred_cost NUMERIC(24, 6) NOT NULL, 
    equity_contribution NUMERIC(24, 6) NOT NULL, 
    financing_draw NUMERIC(24, 6) NOT NULL, 
    interest_paid NUMERIC(24, 6) NOT NULL, 
    financing_fees NUMERIC(24, 6) NOT NULL, 
    financing_repayment NUMERIC(24, 6) NOT NULL, 
    landowner_payment NUMERIC(24, 6) NOT NULL, 
    developer_distribution NUMERIC(24, 6) NOT NULL, 
    ending_cash NUMERIC(24, 6) NOT NULL, 
    ending_debt NUMERIC(24, 6) NOT NULL, 
    funding_gap NUMERIC(24, 6) NOT NULL, 
    contractual_arrears NUMERIC(24, 6) NOT NULL, 
    cash_balance_variance NUMERIC(24, 6) NOT NULL, 
    data JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_run_month_snapshot UNIQUE (calculation_run_id, month_number), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_monthly_cashflow_snapshots_calculation_run_id ON monthly_cashflow_snapshots (calculation_run_id);

CREATE INDEX ix_monthly_cashflow_snapshots_cashflow_date ON monthly_cashflow_snapshots (cashflow_date);

CREATE INDEX ix_monthly_cashflow_run_date ON monthly_cashflow_snapshots (calculation_run_id, cashflow_date);

CREATE TABLE negotiation_results (
    id VARCHAR(36) NOT NULL, 
    calculation_run_id VARCHAR(36) NOT NULL, 
    method VARCHAR(40) NOT NULL, 
    status VARCHAR(80) NOT NULL, 
    measure_type VARCHAR(20) NOT NULL, 
    fair_floor NUMERIC(24, 6), 
    balanced NUMERIC(24, 6), 
    technical_ceiling NUMERIC(24, 6), 
    negotiation_minimum NUMERIC(24, 6), 
    negotiation_maximum NUMERIC(24, 6), 
    governing_constraint_id VARCHAR(120), 
    recommendation_rank INTEGER, 
    result_snapshot JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_by VARCHAR(36), 
    updated_by VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_run_negotiation_method UNIQUE (calculation_run_id, method), 
    FOREIGN KEY(calculation_run_id) REFERENCES calculation_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_negotiation_results_method ON negotiation_results (method);

CREATE INDEX ix_negotiation_results_calculation_run_id ON negotiation_results (calculation_run_id);

ALTER TABLE calculation_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_calculation_runs_isolation ON calculation_runs;

CREATE POLICY lv360_calculation_runs_isolation ON calculation_runs USING (EXISTS (SELECT 1 FROM projects p WHERE p.id = calculation_runs.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    ))) WITH CHECK (EXISTS (SELECT 1 FROM projects p WHERE p.id = calculation_runs.project_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )));

ALTER TABLE calculation_run_results ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_calculation_run_results_isolation ON calculation_run_results;

CREATE POLICY lv360_calculation_run_results_isolation ON calculation_run_results USING (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = calculation_run_results.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            ) WITH CHECK (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = calculation_run_results.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            );

ALTER TABLE monthly_cashflow_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_monthly_cashflow_snapshots_isolation ON monthly_cashflow_snapshots;

CREATE POLICY lv360_monthly_cashflow_snapshots_isolation ON monthly_cashflow_snapshots USING (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = monthly_cashflow_snapshots.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            ) WITH CHECK (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = monthly_cashflow_snapshots.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            );

ALTER TABLE negotiation_results ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lv360_negotiation_results_isolation ON negotiation_results;

CREATE POLICY lv360_negotiation_results_isolation ON negotiation_results USING (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = negotiation_results.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            ) WITH CHECK (
              EXISTS (
                SELECT 1 FROM calculation_runs cr
                JOIN projects p ON p.id = cr.project_id
                WHERE cr.id = negotiation_results.calculation_run_id AND (
      current_setting('app.can_view_all_projects', true) = 'true'
      OR p.organization_id::text = ANY(string_to_array(current_setting('app.organization_ids', true), ','))
      OR EXISTS (
        SELECT 1 FROM project_assignments a
        WHERE a.project_id = p.id
          AND a.user_id::text = current_setting('app.user_id', true)
          AND a.active IS TRUE
      )
    )
              )
            );

UPDATE alembic_version SET version_num='0006_standalone_financial_portal' WHERE alembic_version.version_num = '0005_fix_project_assignments_rls';

-- Running upgrade 0006_standalone_financial_portal -> 0007_admin_governance_and_security

ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP WITH TIME ZONE NULL;

UPDATE alembic_version SET version_num='0007_admin_governance_and_security' WHERE alembic_version.version_num = '0006_standalone_financial_portal';

COMMIT;

