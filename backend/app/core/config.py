from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    v2_enabled: bool = True
    # "shared_legacy": V2 spends the same per-account daily balance as V1 (no V2 wallet); "wallet": V2's own ledger.
    v2_quota_mode: Literal["wallet", "shared_legacy"] = "wallet"
    v2_new_operations: bool = False
    v2_local_edit_enabled: bool = False
    v2_viewsets_enabled: bool = False
    v2_viewset_authorization_seconds: int = Field(default=86400, ge=300, le=604800)
    v2_alert_stalled_seconds: int = Field(default=300, ge=30, le=86400)
    v2_alert_recent_seconds: int = Field(default=3600, ge=60, le=604800)
    v2_alert_expired_leases: int = Field(default=3, ge=1, le=10000)
    v2_alert_review_failures: int = Field(default=3, ge=1, le=100)
    v2_alert_access_denials: int = Field(default=10, ge=1, le=1000000)
    v2_access_collector_processes: int = Field(default=1, ge=1, le=1024)
    v2_alert_storage_free_bytes: int = Field(default=536870912, ge=0)
    v2_auto_reconcile_enabled: bool = True
    v2_auto_reconcile_max_attempts: int = Field(default=4, ge=1, le=10)
    v2_auto_reconcile_window_seconds: int = Field(default=86400, ge=300, le=604800)
    v2_question_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    v2_submit_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86400)
    v2_submit_rate_limit_max_requests: int = Field(default=6, ge=1, le=10000)
    # Conversations a regular (non-admin) account may keep; archived and deleted ones do not count. 0 = no limit.
    v2_design_limit_per_user: int = Field(default=10, ge=0, le=100000)
    v2_account_file_cleanup_enabled: bool = False
    v2_account_file_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_account_content_cleanup_enabled: bool = False
    v2_account_closure_enabled: bool = False
    v2_account_closure_grace_days: int = Field(default=30, ge=1, le=3650)
    v2_native_history_cleanup_enabled: bool = False
    v2_native_history_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_design_content_cleanup_enabled: bool = False
    v2_trash_asset_cleanup_enabled: bool = False
    v2_trash_asset_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_backup_retention_days: int = Field(default=0, ge=0, le=3650)
    v2_backup_min_copies: int = Field(default=2, ge=2, le=100)
    v2_runtime_retention_days: int = Field(default=0, ge=0, le=3650)
    v2_runtime_intermediate_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_upload_retention_days: int = Field(default=0, ge=0, le=3650)
    v2_expired_upload_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_orphan_retention_days: int = Field(default=0, ge=0, le=3650)
    v2_orphan_grace_days: int = Field(default=7, ge=1, le=3650)
    v2_runtime_mode: str = "disabled"
    v2_local_user_id: str = ""
    v2_worker_user_id: str = ""
    v2_container_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    v2_container_root: str = "/srv/kigcraft/runtime"
    v2_container_image: str = ""
    v2_egress_image: str = ""
    v2_egress_hosts: list[str] = Field(default_factory=list, max_length=64)
    v2_container_uid: int = Field(default=10001, ge=10000)
    v2_container_gid: int = Field(default=10001, ge=10000)
    v2_docker_path: str = "/usr/bin/docker"
    v2_docker_socket: str = "/var/run/docker.sock"
    v2_codex_path: str = "codex"
    v2_codex_version: str = "0.155.0-alpha.16.4"
    v2_local_trial_candidate_enabled: bool = False
    v2_review_model: str = "gpt-6-luna"
    v2_asset_backend: Literal["filesystem", "minio"] = "filesystem"
    v2_s3_endpoint_url: str = ""
    v2_s3_bucket: str = ""
    v2_s3_prefix: str = "v2"
    v2_s3_access_key_id: str = ""
    v2_s3_secret_access_key: str = ""
    v2_s3_region: str = "us-east-1"
    v2_minio_metrics_enabled: bool = False
    v2_minio_metrics_version: Literal["v2", "v3"] = "v3"
    v2_minio_metrics_token: str = ""
    v2_minio_metrics_timeout_seconds: float = Field(default=3, ge=0.1, le=10)
    v2_minio_metrics_max_age_seconds: int = Field(default=300, ge=1, le=3600)
    v2_storage_dir: str = "runtime/v2/assets"
    v2_runtime_dir: str = "runtime/v2/codex"
    v2_max_input_images: int = Field(default=8, ge=1)
    v2_max_input_bytes: int = Field(default=40 * 1024 * 1024, ge=1)
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://kig:kig@postgres:5432/kig_preview"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint_url: str = "http://minio:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    s3_bucket: str = "kig-preview"
    jwt_secret: str = Field(default="dev-change-me")
    mock_sms_code: str = "123456"
    current_terms_version: str = "2026-07-11"
    generation_provider: str = "fixture"
    allow_fixture_generation: bool = False
    fixture_dir: str = "app/static/fixtures"
    codex_path: str = "codex"
    # Seconds between background readings of the Codex quota for the admin chart; 0 turns the sampler off.
    codex_usage_sample_seconds: int = Field(default=600, ge=0)
    codex_update_enabled: bool = True
    codex_update_timeout_seconds: int = 120
    codex_update_status_path: str = "runtime/codex-update-status.json"
    codex_model: str = "gpt-6-astra"
    codex_reasoning_effort: str = "low"
    codex_detail_analysis_model: str = "gpt-6-astra"
    codex_detail_analysis_reasoning_effort: str = "low"
    codex_detail_analysis_timeout_seconds: int = 240
    codex_generation_timeout_seconds: float = Field(default=600, gt=0)
    generation_job_timeout_seconds: float = Field(default=1500, gt=0)
    codex_generation_max_attempts: int = Field(default=2, ge=1, le=4)
    codex_retry_base_seconds: float = Field(default=5, ge=0, le=60)
    codex_ephemeral: bool = True
    codex_session_cleanup_enabled: bool = True
    codex_session_retention_days: int = Field(default=7, ge=1)
    codex_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)
    codex_workspace_dir: str = "runtime/codex"
    codex_output_dir: str = "runtime/generated"
    codex_profiles_dir: str = "runtime/codex-profiles"
    codex_bridge_url: str = ""
    codex_bridge_token: str = ""
    codex_bridge_timeout_seconds: int = 1800
    codex_usage_min_remaining_percent: int = 25
    codex_usage_command: str = ""
    codex_usage_check_enabled: bool = True
    codex_usage_check_timeout_seconds: int = 12
    provider_preset_path: str = "runtime/provider-preset.json"
    codex_product_reference_path: str = "ref/商成品参考图.png"
    reference_upload_dir: str = "runtime/references"
    generated_public_prefix: str = "/api/generated"
    generation_audit_db_path: str = "runtime/generation_audit.sqlite3"
    email_provider: str = "console"
    tencent_cloud_secret_id: str = ""
    tencent_cloud_secret_key: str = ""
    tencent_ses_region: str = "ap-guangzhou"
    tencent_ses_from_email: str = "KigCraft <noreply@example.com>"
    tencent_ses_template_id: str = ""
    tencent_ses_logo_url: str = "https://kigcraft.com/email-logo.png"
    auth_session_ttl_hours: int = 24 * 14
    session_cookie_name: str = "kig_session"
    session_cookie_secure: bool = False
    email_code_ttl_minutes: int = 10
    email_code_max_attempts: int = 5
    email_code_send_cooldown_seconds: int = 60
    email_code_send_window_seconds: int = 3600
    email_code_send_max_per_email: int = 5
    email_code_send_max_per_ip: int = 20
    auth_login_failure_window_seconds: int = 900
    auth_login_failure_max_per_email: int = 5
    auth_login_failure_max_per_ip: int = 50
    captcha_enabled: bool = True
    turnstile_site_key: str = "1x00000000000000000000AA"
    turnstile_secret_key: str = "1x0000000000000000000000000000000AA"
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    turnstile_timeout_seconds: float = 5.0
    generation_parallelism: int = 8
    quota_window_hours: int = 5
    mock_output_dir: str = "runtime/mock-outputs"
    max_provider_concurrency: int = 8
    admin_quota_policy_path: str = "runtime/audit_quota_policy.json"
    cors_allowed_origins: str = ""
    trusted_proxy_hosts: str = "127.0.0.1,::1"
    generation_create_rate_limit_window_seconds: int = 300
    generation_create_rate_limit_max_requests: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
