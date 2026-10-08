import json
import subprocess
from pathlib import Path

import pytest

from app.codex_update import read_codex_update_status, update_codex_cli
from app.core.config import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        codex_path="codex-test",
        codex_update_status_path=str(tmp_path / "codex-update.json"),
        **overrides,
    )


def test_update_codex_cli_records_success(tmp_path: Path) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "codex-cli 0.142.0\n", ""),
            subprocess.CompletedProcess([], 0, "updated\n", ""),
            subprocess.CompletedProcess([], 0, "codex-cli 0.143.0\n", ""),
        ]
    )
    calls: list[tuple[list[str], int]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, int(kwargs["timeout"])))
        return next(results)

    settings = _settings(tmp_path, codex_update_timeout_seconds=17)
    status = update_codex_cli(settings, runner=runner)

    assert status.succeeded is True
    assert status.version_before == "codex-cli 0.142.0"
    assert status.version_after == "codex-cli 0.143.0"
    assert status.error is None
    assert calls == [
        (["codex-test", "--version"], 17),
        (["codex-test", "update"], 17),
        (["codex-test", "--version"], 17),
    ]
    assert read_codex_update_status(settings) == status


@pytest.mark.parametrize(
    "credential",
    [
        "Authorization: Bearer secret-token",
        "sk-proj-super-secret-value",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "https://user:password@example.com/update?token=query-secret",
        "password=hunter2",
    ],
)
def test_update_codex_cli_records_only_allowlisted_nonzero_failure(
    tmp_path: Path, credential: str
) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "codex-cli 0.142.0\n", ""),
            subprocess.CompletedProcess([], 1, "", credential + "\n" + "x" * 700),
        ]
    )

    def failing_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return next(results)

    settings = _settings(tmp_path)
    status = update_codex_cli(settings, runner=failing_runner)

    assert status.succeeded is False
    assert status.version_before == "codex-cli 0.142.0"
    assert status.version_after is None
    assert status.error == "update_failed_exit_1"
    assert read_codex_update_status(settings) == status
    raw_status = Path(settings.codex_update_status_path).read_text(encoding="utf-8")
    assert credential not in raw_status
    assert set(json.loads(raw_status)) == {
        "checked_at",
        "version_before",
        "version_after",
        "succeeded",
        "error",
    }


def test_update_codex_cli_records_timeout_without_raising(tmp_path: Path) -> None:
    results: list[object] = [
        subprocess.CompletedProcess([], 0, "codex-cli 0.142.0\n", ""),
        subprocess.TimeoutExpired(["codex-test", "update"], timeout=3),
    ]

    def timing_out_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    settings = _settings(tmp_path, codex_update_timeout_seconds=3)
    status = update_codex_cli(settings, runner=timing_out_runner)

    assert status.succeeded is False
    assert status.version_before == "codex-cli 0.142.0"
    assert status.error == "update_timed_out"
    assert read_codex_update_status(settings) == status


def test_update_codex_cli_records_fixed_version_check_failure(tmp_path: Path) -> None:
    def failing_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 9, "sk-proj-output", "password=hunter2")

    settings = _settings(tmp_path)
    status = update_codex_cli(settings, runner=failing_runner)

    assert status.succeeded is False
    assert status.error == "version_check_failed_exit_9"
    assert "hunter2" not in Path(settings.codex_update_status_path).read_text(encoding="utf-8")


def test_update_codex_cli_records_codex_not_found(tmp_path: Path) -> None:
    def missing_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("password=hunter2")

    settings = _settings(tmp_path)
    status = update_codex_cli(settings, runner=missing_runner)

    assert status.succeeded is False
    assert status.error == "codex_not_found"
    assert "hunter2" not in Path(settings.codex_update_status_path).read_text(encoding="utf-8")


def test_update_codex_cli_rejects_raw_version_output(tmp_path: Path) -> None:
    def leaking_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "codex-cli 0.142.0 password=hunter2", "")

    settings = _settings(tmp_path)
    status = update_codex_cli(settings, runner=leaking_runner)

    assert status.succeeded is False
    assert status.version_before is None
    assert status.error == "version_check_invalid_output"
    assert "hunter2" not in Path(settings.codex_update_status_path).read_text(encoding="utf-8")


def test_update_codex_cli_disabled_records_skipped_success(tmp_path: Path) -> None:
    def unexpected_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("runner must not be called")

    settings = _settings(tmp_path, codex_update_enabled=False)
    status = update_codex_cli(settings, runner=unexpected_runner)

    assert status.succeeded is True
    assert status.version_before is None
    assert status.version_after is None
    assert status.error is None
    assert read_codex_update_status(settings) == status
