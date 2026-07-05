from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://kig:change-me-postgres-password@postgres:5432/kig_preview"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint_url: str = "http://minio:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "kig_minio"
    s3_secret_access_key: str = "change-me-minio-password"
    s3_bucket: str = "kig-preview"
    jwt_secret: str = Field(default="change-me-generate-a-long-random-secret")
    mock_sms_code: str = "000000"
    generation_provider: str = "fixture"
    allow_fixture_generation: bool = False
    fixture_dir: str = "app/static/fixtures"
    codex_path: str = "codex"
    codex_detail_analysis_model: str = "gpt-5.5"
    codex_detail_analysis_reasoning_effort: str = "high"
    codex_detail_analysis_timeout_seconds: int = 240
    codex_workspace_dir: str = "runtime/codex"
    codex_output_dir: str = "runtime/generated"
    codex_bridge_url: str = ""
    codex_bridge_token: str = ""
    codex_bridge_timeout_seconds: int = 1800
    codex_usage_min_remaining_percent: int = 25
    codex_usage_command: str = ""
    codex_usage_check_enabled: bool = True
    codex_usage_check_timeout_seconds: int = 12
    codex_product_reference_path: str = "ref/product-reference.png"
    reference_upload_dir: str = "runtime/references"
    generated_public_prefix: str = "/api/generated"
    generation_audit_db_path: str = "runtime/generation_audit.sqlite3"
    generation_parallelism: int = 8
    quota_window_hours: int = 5
    mock_output_dir: str = "runtime/mock-outputs"
    max_provider_concurrency: int = 8
    normal_quota_window_hours: int = 5
    normal_base_quota: int = 2
    admin_audit_enabled: bool = True
    admin_audit_password: str = "change-me-admin-audit-password"
    admin_audit_session_hours: int = 12
    admin_audit_max_login_attempts: int = 5
    admin_audit_retry_window_minutes: int = 15
    cors_allowed_origins: str = ""
    trusted_proxy_hosts: str = "127.0.0.1,::1"
    generation_create_rate_limit_window_seconds: int = 300
    generation_create_rate_limit_max_requests: int = 3
    watermark_text: str = "KigCraft AI generated"
    watermark_domain_text: str = "KigCraft"


@lru_cache
def get_settings() -> Settings:
    return Settings()
