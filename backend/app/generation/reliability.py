"""Per-job reliability telemetry; no prompts, user data or raw errors are stored."""

import errno
from collections import Counter
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field


@dataclass
class GenerationMetrics:
    version: int = 1
    duration_seconds: float = 0
    parallelism: int = 0
    active_jobs_at_start: int = 0
    attempts: int = 0
    attempts_known: bool = False
    cli_exit_recovered: bool = False
    stage: str = "preparing_references"
    image_generated: bool = False
    manifest_recovered: bool = False
    publication_retries: int = 0
    failure_code: str | None = None
    attempt_failures: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        return {"reliability": asdict(self)}


current_metrics: ContextVar[GenerationMetrics | None] = ContextVar(
    "generation_metrics", default=None
)


def classify_failure(exc: BaseException | str) -> str:
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "disk_full"
    if isinstance(exc, TimeoutError):
        return "timeout"
    value = str(exc).lower()
    categories = (
        ("disk_full", ("no space left", "disk is full", "disk full")),
        ("auth", ("codex_auth", "401", "unauthorized", "token_expired", "refresh_token")),
        ("quota", ("usage limit", "usage_limit", "insufficient_quota", "quota exceeded")),
        (
            "input_rejected",
            (
                "unusable_reference",
                "adult_explicit",
                "content_policy",
                "safety rejection",
                "safety policy",
                "request rejected",
            ),
        ),
        ("rate_limit", ("429", "rate limit", "rate_limit")),
        ("timeout", ("timed out", "timeout")),
        (
            "connection",
            (
                "websocket",
                "connection",
                "tls handshake",
                "failed to connect",
                "network",
                "502",
                "503",
                "504",
            ),
        ),
        ("tool_missing", ("did not use the image generation tool",)),
        ("no_image", ("did not return an image", "codex_no_image")),
        ("manifest_invalid", ("manifest",)),
    )
    for code, markers in categories:
        if any(marker in value for marker in markers):
            return code
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return "configuration"
    if isinstance(exc, ValueError):
        return "invalid_output"
    return "unknown"


RETRYABLE_FAILURES = {"connection", "timeout", "rate_limit", "no_image", "tool_missing", "unknown"}


def summarize_reliability(jobs) -> dict:
    """Only instrumented completed jobs enter the denominator; legacy is unknown."""
    completed = first = succeeded = retried = retry_success = image_count = save_failed = (
        recovered
    ) = unknown = 0
    measured_attempts = cli_recovered = 0
    failures: Counter[str] = Counter()
    attempt_failures: Counter[str] = Counter()
    for job in jobs:
        if job.status not in {"succeeded", "accepted", "failed"}:
            continue
        metrics = next(
            (
                event.payload.get("reliability")
                for event in reversed(job.events)
                if event.payload.get("reliability", {}).get("version") == 1
            ),
            None,
        )
        if not metrics:
            unknown += 1
            continue
        completed += 1
        ok = job.status in {"succeeded", "accepted"}
        attempts = metrics.get("attempts", 0)
        succeeded += int(ok)
        known = metrics.get("attempts_known", attempts > 0)
        measured_attempts += int(known)
        cli_recovered += int(ok and metrics.get("cli_exit_recovered", False))
        first += int(
            ok
            and known
            and attempts == 1
            and not metrics.get("manifest_recovered")
            and not metrics.get("publication_retries")
            and not metrics.get("cli_exit_recovered")
        )
        retried += int(attempts > 1)
        retry_success += int(ok and attempts > 1)
        image_count += int(metrics.get("image_generated", False))
        save_failed += int(
            not ok
            and metrics.get("image_generated", False)
            and metrics.get("stage") == "saving_outputs"
        )
        recovered += int(ok and metrics.get("manifest_recovered", False))
        if not ok:
            failures[metrics.get("failure_code") or "unknown"] += 1
        attempt_failures.update(metrics.get("attempt_failures", []))

    def rate(n, d):
        return round(n / d * 100, 2) if d else None

    return {
        "completed_jobs": completed,
        "uninstrumented_jobs": unknown,
        "first_pass_successes": first,
        "final_successes": succeeded,
        "retried_jobs": retried,
        "retry_successes": retry_success,
        "images_generated_jobs": image_count,
        "save_failed_jobs": save_failed,
        "manifest_recovered_jobs": recovered,
        "cli_exit_recovered_jobs": cli_recovered,
        "attempt_instrumented_jobs": measured_attempts,
        "first_pass_success_rate": rate(first, measured_attempts),
        "final_success_rate": rate(succeeded, completed),
        "retry_recovery_rate": rate(retry_success, retried),
        "save_failure_rate": rate(save_failed, image_count),
        "failure_counts": dict(failures),
        "attempt_failure_counts": dict(attempt_failures),
    }
