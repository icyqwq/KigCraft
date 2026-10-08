"""User-facing message codes. Copy is resolved on the frontend via i18n."""

QUEUED = "queued"
PREPARING_REFERENCES = "preparing_references"
GENERATING = "generating"
SAVING_OUTPUTS = "saving_outputs"
GENERATION_COMPLETE = "generation_complete"
GENERATION_FAILED = "generation_failed"
CANCELLED = "cancelled"
CANDIDATE_ACCEPTED_STOPPED = "candidate_accepted_stopped"
SERVICE_RESTARTED_INTERRUPTED = "service_restarted_interrupted"
GENERATION_RATE_LIMITED = "generation_rate_limited"

CODEX_USAGE_LIMIT = "codex_usage_limit"
CODEX_AUTH_MISSING = "codex_auth_missing"
CODEX_AUTH_EXPIRED = "codex_auth_expired"
CODEX_CONNECTION_FAILED = "codex_connection_failed"
CODEX_NO_IMAGE = "codex_no_image"
CONTENT_POLICY = "content_policy"
CODEX_USAGE_LOW = "codex_usage_low"


def candidate_ready(index: int) -> str:
    return f"candidate_ready:{index}"


def candidate_progress(completed: int, total: int) -> str:
    return f"candidate_progress:{completed}/{total}"


def codex_usage_limit(retry_at: str | None = None) -> str:
    if retry_at:
        return f"{CODEX_USAGE_LIMIT}:retry_at={retry_at}"
    return CODEX_USAGE_LIMIT


def codex_usage_limit_from_snapshot() -> str:
    from app.core.config import get_settings
    from app.generation.codex_usage import format_reset_at, format_wait_time, get_cached_codex_usage_snapshot

    snapshot = get_cached_codex_usage_snapshot(get_settings())
    window = snapshot.five_hour if snapshot and snapshot.available else None
    retry_at = None
    if window is not None:
        if window.reset_at is not None:
            retry_at = format_reset_at(window.reset_at)
        elif window.reset_after_seconds is not None:
            retry_at = format_wait_time(window.reset_after_seconds)
    return codex_usage_limit(retry_at)
