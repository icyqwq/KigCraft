"""Shared quota settings without importing legacy execution or HTTP routers."""
import json
from pathlib import Path
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.core.paths import resolve_repo_path

class QuotaPolicy(BaseModel):
    window_hours: int = Field(default=5, ge=1, le=168)
    window_limit: int = Field(default=8, ge=1, le=1000)
    parallel_generation_limit: int = Field(default=4, ge=1, le=64)
    codex_usage_min_remaining_percent: int = Field(default=25, ge=0, le=100)
    daily_generation_limit: int = Field(default=20, ge=0, le=100000)
    daily_generation_custom_limit_default: int = Field(default=20, ge=0, le=100000)
    daily_generation_reset_hour: int = Field(default=6, ge=0, le=23)

_quota_policy: QuotaPolicy | None = None


def clear_quota_policy() -> None:
    global _quota_policy
    _quota_policy = None


def _quota_policy_path() -> Path:
    return resolve_repo_path(get_settings().admin_quota_policy_path)

def _load_quota_policy() -> QuotaPolicy:
    policy_path = _quota_policy_path()
    if not policy_path.is_file():
        return QuotaPolicy()
    try:
        return QuotaPolicy.model_validate(json.loads(policy_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return QuotaPolicy()

def get_quota_policy() -> QuotaPolicy:
    global _quota_policy
    if _quota_policy is None:
        _quota_policy = _load_quota_policy()
    return _quota_policy

def save_quota_policy(policy: QuotaPolicy) -> QuotaPolicy:
    global _quota_policy
    policy_path = _quota_policy_path()
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = policy_path.with_name(f"{policy_path.name}.tmp")
    temp_path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    temp_path.replace(policy_path)
    _quota_policy = policy
    return policy
