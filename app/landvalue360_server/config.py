"""Environment-driven configuration for local and cloud deployments."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path


_ALLOWED_EDITIONS = {"combined", "developer", "government", "administration"}
_ALLOWED_GOVERNMENT_WORKFLOWS = {"direct", "institutional"}


def _csv_tuple(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "LandValue360 Platform API"
    environment: str = "development"
    edition_mode: str = "combined"
    database_url: str = "sqlite+pysqlite:///./.landvalue360/landvalue360.db"
    token_ttl_minutes: int = 480
    password_iterations: int = 310_000
    auto_create_schema: bool = False
    enable_docs: bool = True
    bootstrap_email: str | None = None
    bootstrap_password: str | None = None
    bootstrap_organization: str = "LandValue360 Development"
    bootstrap_organization_slug: str = "default"
    bootstrap_workspace: str = "Main Workspace"
    bootstrap_workspace_slug: str = "main"
    evidence_storage_dir: str = "./.landvalue360/evidence"
    max_evidence_file_bytes: int = 25 * 1024 * 1024
    max_project_package_bytes: int = 25 * 1024 * 1024
    max_project_package_uncompressed_bytes: int = 100 * 1024 * 1024
    max_project_package_entries: int = 250
    max_project_package_compression_ratio: int = 100
    max_excel_import_bytes: int = 25 * 1024 * 1024
    max_json_depth: int = 80
    trusted_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    behind_https_proxy: bool = False
    login_rate_limit_attempts: int = 8
    login_rate_limit_window_seconds: int = 300
    enable_access_log: bool = False
    government_workflow_mode: str = "direct"

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("LV360_ENV", "development").strip().lower()
        edition_mode = os.getenv("LV360_EDITION_MODE", "combined").strip().lower()
        default_database = "sqlite+pysqlite:///./.landvalue360/landvalue360.db"
        default_hosts = "127.0.0.1,localhost,testserver" if environment in {"development", "test"} else ""
        settings = cls(
            app_name=os.getenv("LV360_APP_NAME", cls.app_name),
            environment=environment,
            edition_mode=edition_mode,
            database_url=os.getenv("LV360_DATABASE_URL", default_database),
            token_ttl_minutes=int(os.getenv("LV360_TOKEN_TTL_MINUTES", "480")),
            password_iterations=int(os.getenv("LV360_PASSWORD_ITERATIONS", "310000")),
            auto_create_schema=os.getenv("LV360_AUTO_CREATE_SCHEMA", "0") == "1",
            enable_docs=os.getenv("LV360_ENABLE_DOCS", "1") == "1",
            bootstrap_email=os.getenv("LV360_BOOTSTRAP_EMAIL") or None,
            bootstrap_password=os.getenv("LV360_BOOTSTRAP_PASSWORD") or None,
            bootstrap_organization=os.getenv("LV360_BOOTSTRAP_ORGANIZATION", "LandValue360 Development"),
            bootstrap_organization_slug=os.getenv("LV360_BOOTSTRAP_ORGANIZATION_SLUG", "default"),
            bootstrap_workspace=os.getenv("LV360_BOOTSTRAP_WORKSPACE", "Main Workspace"),
            bootstrap_workspace_slug=os.getenv("LV360_BOOTSTRAP_WORKSPACE_SLUG", "main"),
            evidence_storage_dir=os.getenv("LV360_EVIDENCE_STORAGE_DIR", "./.landvalue360/evidence"),
            max_evidence_file_bytes=int(os.getenv("LV360_MAX_EVIDENCE_FILE_BYTES", str(25 * 1024 * 1024))),
            max_project_package_bytes=int(os.getenv("LV360_MAX_PROJECT_PACKAGE_BYTES", str(25 * 1024 * 1024))),
            max_project_package_uncompressed_bytes=int(os.getenv("LV360_MAX_PROJECT_PACKAGE_UNCOMPRESSED_BYTES", str(100 * 1024 * 1024))),
            max_project_package_entries=int(os.getenv("LV360_MAX_PROJECT_PACKAGE_ENTRIES", "250")),
            max_project_package_compression_ratio=int(os.getenv("LV360_MAX_PROJECT_PACKAGE_COMPRESSION_RATIO", "100")),
            max_excel_import_bytes=int(os.getenv("LV360_MAX_EXCEL_IMPORT_BYTES", str(25 * 1024 * 1024))),
            max_json_depth=int(os.getenv("LV360_MAX_JSON_DEPTH", "80")),
            trusted_hosts=_csv_tuple(os.getenv("LV360_TRUSTED_HOSTS", default_hosts)),
            behind_https_proxy=os.getenv("LV360_BEHIND_HTTPS_PROXY", "0") == "1",
            login_rate_limit_attempts=int(os.getenv("LV360_LOGIN_RATE_LIMIT_ATTEMPTS", "8")),
            login_rate_limit_window_seconds=int(os.getenv("LV360_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")),
            enable_access_log=os.getenv("LV360_ENABLE_ACCESS_LOG", "0") == "1",
            government_workflow_mode={"simple": "direct"}.get(
                os.getenv(
                    "LV360_LANDOWNER_WORKFLOW_MODE",
                    os.getenv("LV360_GOVERNMENT_WORKFLOW_MODE", "direct"),
                ).strip().lower(),
                os.getenv(
                    "LV360_LANDOWNER_WORKFLOW_MODE",
                    os.getenv("LV360_GOVERNMENT_WORKFLOW_MODE", "direct"),
                ).strip().lower(),
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.edition_mode not in _ALLOWED_EDITIONS:
            raise ValueError("LV360_EDITION_MODE must be combined, developer, government, or administration.")
        if self.government_workflow_mode not in _ALLOWED_GOVERNMENT_WORKFLOWS:
            raise ValueError("LV360_LANDOWNER_WORKFLOW_MODE must be direct or institutional.")
        if self.token_ttl_minutes <= 0:
            raise ValueError("LV360_TOKEN_TTL_MINUTES must be positive.")
        if self.password_iterations < 10_000:
            raise ValueError("LV360_PASSWORD_ITERATIONS must be at least 10000.")
        if self.max_evidence_file_bytes <= 0:
            raise ValueError("LV360_MAX_EVIDENCE_FILE_BYTES must be positive.")
        if self.max_project_package_bytes <= 0 or self.max_project_package_uncompressed_bytes <= 0:
            raise ValueError("Project-package size limits must be positive.")
        if self.max_project_package_entries < 1 or self.max_project_package_compression_ratio < 1:
            raise ValueError("Project-package entry and compression limits must be positive.")
        if self.max_excel_import_bytes <= 0 or self.max_json_depth < 1:
            raise ValueError("Import size and JSON depth limits must be positive.")
        if self.login_rate_limit_attempts < 1 or self.login_rate_limit_window_seconds < 1:
            raise ValueError("Login rate-limit settings must be positive.")
        if self.environment in {"production", "staging"}:
            if self.database_url.startswith("sqlite"):
                raise ValueError("Production and staging require PostgreSQL, not SQLite.")
            if self.auto_create_schema:
                raise ValueError("Production must use Alembic migrations, not auto-create schema.")
            if not self.trusted_hosts:
                raise ValueError("LV360_TRUSTED_HOSTS is required in production and staging.")

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def edition_scope(self) -> str:
        return self.edition_mode.upper()

    @property
    def evidence_storage_path(self) -> Path:
        return Path(self.evidence_storage_dir).expanduser().resolve()

    @property
    def database_file(self) -> Path | None:
        prefix = "sqlite+pysqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        if raw == ":memory:":
            return None
        return Path(raw).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
