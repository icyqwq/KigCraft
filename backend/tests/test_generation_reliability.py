import asyncio
import errno
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image

from app.generation import provider as p
from app.generation.reliability import (
    GenerationMetrics,
    current_metrics,
    classify_failure,
    summarize_reliability,
)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    reference = tmp_path / "references/upload/front.webp"
    reference.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(reference)
    settings = SimpleNamespace(
        codex_path="codex",
        codex_workspace_dir=str(tmp_path / "codex"),
        codex_output_dir=str(tmp_path / "generated"),
        generated_public_prefix="/api/generated",
        reference_upload_dir=str(tmp_path / "references"),
        codex_product_reference_path=str(reference),
        codex_generation_max_attempts=2,
        codex_retry_base_seconds=0,
        codex_generation_timeout_seconds=0.02,
        generation_job_timeout_seconds=1,
    )
    monkeypatch.setattr(p, "get_settings", lambda: settings)
    monkeypatch.setattr(p, "apply_kigcraft_watermark", lambda *a, **kw: True)
    payload = {
        "character_session_id": "session",
        "reference_keys": ["front:references/upload/front.webp"],
        "system_constraints": [],
        "user_requirements": [],
        "user_notes": "",
    }
    return settings, payload, tmp_path / "codex/session/job"


def make_image(workspace, manifest=True):
    (workspace / "outputs").mkdir(exist_ok=True)
    Image.new("RGB", (16, 16)).save(workspace / "outputs/candidate-1.webp")
    if manifest:
        (workspace / "manifest.json").write_text(
            json.dumps(
                {
                    "generation_source": "image_generation_tool",
                    "outputs": [{"index": 1, "path": "outputs/candidate-1.webp"}],
                }
            ),
            encoding="utf-8",
        )


EVIDENCE = b'{"type":"item.completed","item":{"type":"image_generation_call"}}'


async def run_with_metrics(payload):
    metrics = GenerationMetrics()
    token = current_metrics.set(metrics)
    try:
        outputs = await p.CodexImageProvider().generate("job", payload)
        return outputs, metrics
    finally:
        current_metrics.reset(token)


async def test_successful_exit_without_image_retries(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        if len(calls) == 2:
            make_image(workspace)
        return EVIDENCE, b"", 0

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    outputs, metrics = await run_with_metrics(payload)
    assert len(outputs) == 1 and len(calls) == 2
    assert metrics.attempt_failures == ["no_image"]
    assert metrics.attempts == 2


@pytest.mark.parametrize("manifest", ["missing", "invalid"])
async def test_existing_image_repairs_manifest_without_regeneration(harness, monkeypatch, manifest):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        make_image(workspace, manifest=False)
        if manifest == "invalid":
            (workspace / "manifest.json").write_text("{broken", encoding="utf-8")
        return EVIDENCE, b"", 0

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    outputs, metrics = await run_with_metrics(payload)
    assert len(outputs) == 1 and len(calls) == 1
    assert metrics.manifest_recovered


@pytest.mark.parametrize(
    "message,code",
    [
        ("401 unauthorized", "auth"),
        ("usage limit", "quota"),
        ("content_policy violation", "input_rejected"),
        ("No space left on device", "disk_full"),
    ],
)
async def test_permanent_failures_do_not_retry(harness, monkeypatch, message, code):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        raise RuntimeError(message)

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    with pytest.raises(RuntimeError):
        await run_with_metrics(payload)
    assert len(calls) == 1
    assert classify_failure(message) == code


async def test_publication_retry_keeps_existing_image(harness, monkeypatch):
    settings, payload, workspace = harness
    generations = []
    copies = []

    async def attempt(*args):
        generations.append(1)
        make_image(workspace)
        return EVIDENCE, b"", 0

    original = p._copy_public_outputs

    def copy(*args, **kwargs):
        copies.append(1)
        if len(copies) == 1:
            raise OSError(errno.EBUSY, "busy")
        return original(*args, **kwargs)

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    monkeypatch.setattr(p, "_copy_public_outputs", copy)
    outputs, metrics = await run_with_metrics(payload)
    assert len(outputs) == 1 and len(generations) == 1 and len(copies) == 2
    assert metrics.publication_retries == 1


async def test_disk_full_during_publication_does_not_regenerate(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        make_image(workspace)
        return EVIDENCE, b"", 0

    def copy(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left")

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    monkeypatch.setattr(p, "_copy_public_outputs", copy)
    metrics = GenerationMetrics()
    token = current_metrics.set(metrics)
    try:
        with pytest.raises(OSError):
            await p.CodexImageProvider().generate("job", payload)
    finally:
        current_metrics.reset(token)
    assert len(calls) == 1 and metrics.stage == "saving_outputs" and metrics.image_generated


async def test_timeout_kills_stubborn_process_and_reaps(tmp_path, monkeypatch):
    class Process:
        returncode = None
        terminated = killed = waited = False

        async def communicate(self):
            await asyncio.Future()

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                raise TimeoutError()
            self.waited = True
            return self.returncode

    process = Process()
    with pytest.raises(RuntimeError, match="timed out"):
        await p._communicate_with_heartbeat(
            process, job_id="job", workspace=tmp_path, timeout_seconds=0.01
        )
    assert process.terminated and process.killed and process.waited


async def test_task_cancellation_reaps_process(tmp_path):
    class Process:
        returncode = None
        terminated = False

        async def communicate(self):
            await asyncio.Future()

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            return self.returncode

    process = Process()
    task = asyncio.create_task(
        p._communicate_with_heartbeat(process, job_id="job", workspace=tmp_path)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated


async def test_total_deadline_bounds_retries(harness, monkeypatch):
    settings, payload, workspace = harness
    settings.generation_job_timeout_seconds = 0.01

    async def attempt(*args):
        await asyncio.Future()

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    with pytest.raises(TimeoutError):
        await run_with_metrics(payload)


def test_recovery_does_not_invent_local_edit_provenance(tmp_path):
    make_image(tmp_path, manifest=False)
    assert not p._recover_codex_manifest(tmp_path, [1], True)
    assert not (tmp_path / "manifest.json").exists()


def test_recovery_rejects_explicit_manual_source(tmp_path):
    make_image(tmp_path)
    (tmp_path / "manifest.json").write_text('{"generation_source":"manual"}', encoding="utf-8")
    assert not p._recover_codex_manifest(tmp_path, [1], False)


def test_reliability_denominators_exclude_legacy_cancelled_and_active():
    def job(status, **metrics):
        events = (
            [SimpleNamespace(payload={"reliability": {"version": 1, **metrics}})] if metrics else []
        )
        return SimpleNamespace(status=status, events=events)

    result = summarize_reliability(
        [
            job("succeeded", attempts=1, image_generated=True),
            job("succeeded", attempts=2, image_generated=True, attempt_failures=["connection"]),
            job(
                "failed",
                attempts=1,
                image_generated=True,
                stage="saving_outputs",
                failure_code="disk_full",
            ),
            job("failed", attempts=2, failure_code="timeout"),
            job("succeeded"),
            job("cancelled", attempts=1),
            job("generating", attempts=1),
        ]
    )
    assert result["completed_jobs"] == 4 and result["uninstrumented_jobs"] == 1
    assert result["first_pass_success_rate"] == 25
    assert result["final_success_rate"] == 50
    assert result["retry_recovery_rate"] == 50
    assert result["save_failure_rate"] == 33.33
    assert result["failure_counts"] == {"disk_full": 1, "timeout": 1}


def test_empty_metrics_are_unknown_not_zero_success():
    assert summarize_reliability([])["final_success_rate"] is None


async def test_cli_nonzero_exit_recovers_completed_image(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        make_image(workspace)
        return EVIDENCE, b"temporary websocket failure", 1

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    outputs, metrics = await run_with_metrics(payload)
    assert len(outputs) == 1 and len(calls) == 1 and metrics.cli_exit_recovered


async def test_structured_reference_rejection_does_not_retry(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        (workspace / "failure.json").write_text('{"code":"unusable_reference"}', encoding="utf-8")
        return b"", b"", 0

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    with pytest.raises(RuntimeError, match="unusable_reference"):
        await run_with_metrics(payload)
    assert len(calls) == 1


async def test_retry_cannot_reuse_previous_attempts_image(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        if len(calls) == 1:
            make_image(workspace)
            (workspace / "outputs/candidate-1.webp").write_bytes(b"incomplete image")
            return b"", b"", 0  # incomplete and no provenance; must not publish
        assert not (workspace / "outputs/candidate-1.webp").exists()
        assert not (workspace / "manifest.json").exists()
        return EVIDENCE, b"", 0

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    with pytest.raises(RuntimeError, match="did not return an image"):
        await run_with_metrics(payload)
    assert len(calls) == 2
    assert not (Path(settings.codex_output_dir) / "session/job/outputs/candidate-1.webp").exists()


@pytest.mark.parametrize("ephemeral", [True, False])
def test_ephemeral_flag_is_configurable(harness, ephemeral):
    settings, _, _ = harness
    settings.codex_ephemeral = ephemeral
    command = p._build_codex_command(settings, Path("workspace"), [], "prompt")
    assert ("--ephemeral" in command) == ephemeral


def test_recovery_requires_typed_completed_tool_event():
    assert p._codex_events_include_completed_image_generation(EVIDENCE)
    assert not p._codex_events_include_completed_image_generation(
        b'{"type":"item.completed","item":{"type":"agent_message","text":"image_generation_call"}}'
    )
    assert not p._codex_events_include_completed_image_generation(
        b'{"type":"item.started","item":{"type":"image_generation_call"}}'
    )
    assert not p._codex_events_include_completed_image_generation(
        b'{"type":"item.completed","item":{"type":"image_generation_call","status":"failed"}}'
    )


async def test_queue_timeout_releases_slot_and_persists_metrics(monkeypatch):
    from app.generation import queue as q

    class Store:
        def __init__(self):
            self.jobs = {
                name: SimpleNamespace(
                    id=name,
                    status="queued",
                    progress=0,
                    prompt_payload={},
                    provider="test",
                    expected_output_count=1,
                    outputs=[],
                    events=[],
                    accepted_output_index=None,
                    stop_requested=False,
                )
                for name in ("slow", "next")
            }

        async def get(self, job_id):
            return self.jobs[job_id]

        async def should_stop(self, job_id):
            return False

        async def set_queue_position(self, *args):
            pass

        async def update(self, job_id, **values):
            job = self.jobs[job_id]
            job.status = values["status"]
            job.progress = values["progress"]
            job.events.append(SimpleNamespace(payload=values.get("payload", {})))
            return job

        async def append_output(self, job_id, output):
            self.jobs[job_id].outputs.append(output)

    class Provider:
        name = "test"

        async def generate_incremental(self, job_id, payload):
            metrics = current_metrics.get()
            metrics.attempts = 1
            metrics.attempts_known = True
            metrics.stage = "generating"
            if job_id == "slow":
                await asyncio.Future()
            yield p.ProviderOutput(index=1, object_key="output", image_url="/output")

    store = Store()
    monkeypatch.setattr(q, "job_store", store)
    monkeypatch.setattr(
        q, "get_settings", lambda: SimpleNamespace(generation_job_timeout_seconds=0.08)
    )
    queue = q.GenerationQueue(parallelism=1)
    await asyncio.wait_for(
        asyncio.gather(queue.run_job("slow", Provider()), queue.run_job("next", Provider())), 1
    )
    assert queue.status().active_count == queue.status().pending_count == 0
    assert store.jobs["slow"].status == "failed" and store.jobs["next"].status == "succeeded"
    assert store.jobs["slow"].events[-1].payload["reliability"]["failure_code"] == "timeout"
    summary = summarize_reliability(store.jobs.values())
    assert summary["final_success_rate"] == 50
    assert current_metrics.get() is None


async def test_real_process_is_reaped_after_timeout(tmp_path):
    import sys

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            await p._communicate_with_heartbeat(
                process, job_id="real-timeout", workspace=tmp_path, timeout_seconds=0.05
            )
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_reliability_payload_persists_and_admin_summary_exposes_it(async_client):
    from app.auth.admin_cli import create_or_update_admin
    from app.generation.job_store import job_store
    from app.generation.schemas import CreateJobRequest

    await create_or_update_admin("reliability-admin@example.com", "AdminPassphrase7!")
    login = await async_client.post(
        "/api/auth/login/password",
        json={"email": "reliability-admin@example.com", "password": "AdminPassphrase7!"},
    )
    assert login.status_code == 200
    job = await job_store.create(
        CreateJobRequest(character_session_id="reliability-test", free_text=""),
        provider="codex",
        user_id=login.json()["id"],
    )
    metrics = GenerationMetrics(
        attempts=2,
        attempts_known=True,
        image_generated=True,
        attempt_failures=["connection"],
        stage="saving_outputs",
    )
    await job_store.update(
        job.id,
        status="succeeded",
        progress=100,
        phase_label="generation_complete",
        event_type="succeeded",
        payload=metrics.payload(),
    )
    persisted = await job_store.get(job.id)
    assert persisted.events[-1].payload["reliability"]["attempts"] == 2
    response = await async_client.get("/api/admin/audit/summary")
    assert response.status_code == 200
    data = response.json()["reliability"]
    assert data["completed_jobs"] == 1
    assert data["final_success_rate"] == 100 and data["first_pass_success_rate"] == 0
    assert data["retry_recovery_rate"] == 100


async def test_timeout_with_existing_image_does_not_blindly_regenerate(harness, monkeypatch):
    settings, payload, workspace = harness
    calls = []

    async def attempt(*args):
        calls.append(1)
        make_image(workspace, manifest=False)
        raise TimeoutError()

    monkeypatch.setattr(p, "_run_codex_generation_attempt", attempt)
    with pytest.raises(TimeoutError):
        await run_with_metrics(payload)
    assert len(calls) == 1


async def test_cancel_all_interrupts_hanging_provider(monkeypatch):
    from app.generation import queue as q

    started = asyncio.Event()
    stopped = asyncio.Event()

    class Store:
        async def cancel(self, job_id):
            pass

    queue = q.GenerationQueue()

    async def work():
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    task = asyncio.create_task(work())
    queue._tasks["job"] = task
    monkeypatch.setattr(q, "job_store", Store())
    await started.wait()
    await asyncio.wait_for(queue.cancel_all_and_wait(), 0.5)
    assert stopped.is_set() and task.cancelled()
