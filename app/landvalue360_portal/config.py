from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LV360_PORTAL_", env_file=".env", extra="ignore")

    env: str = "development"
    secret_key: str = "change-this-to-a-long-random-secret"
    base_url: str = "http://127.0.0.1:8090"
    database_url: str = "sqlite+pysqlite:///./data/portal.db"
    storage_backend: str = "local"
    local_storage_path: str = "./data/private"
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "lv360-private"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    email_backend: str = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "notifications@example.com"
    smtp_starttls: bool = True
    auto_verify_email: bool = True
    session_hours: int = 12
    max_upload_mb: int = 25
    project_storage_mb: int = 250
    max_package_mb: int = 50
    sla_hours: int = 24
    timezone: str = "Asia/Damascus"
    cookie_secure: bool = False
    trusted_hosts: str = "127.0.0.1,localhost"


    @model_validator(mode="after")
    def production_safety(self):
        if self.is_production:
            if self.secret_key == "change-this-to-a-long-random-secret" or len(self.secret_key) < 48:
                raise ValueError("Production requires a strong LV360_PORTAL_SECRET_KEY of at least 48 characters")
            if not self.base_url.startswith("https://"):
                raise ValueError("Production LV360_PORTAL_BASE_URL must use HTTPS")
            if not self.cookie_secure:
                raise ValueError("Production requires secure cookies")
            if self.database_url.startswith("sqlite"):
                raise ValueError("Production requires PostgreSQL; SQLite is local-development only")
            if self.storage_backend.lower() != "s3":
                raise ValueError("Production requires private S3-compatible object storage")
        return self

    @property
    def storage_path(self) -> Path:
        return Path(self.local_storage_path).resolve()

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
