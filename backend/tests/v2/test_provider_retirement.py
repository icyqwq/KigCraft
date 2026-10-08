"""Retired code cannot execute; historical records remain available."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.config import Settings, get_settings
from app.generation.presets import ACTIVE_PRESETS, ActiveProviderPresetStore, resolve_preset, snapshot_for_preset
from app.generation.provider import get_generation_provider, get_provider_for_preset
from app.generation.retired import RetiredProviderError
from app.generation.job_repository import JobRepository
from app.generation.router import _provider_for_new_operation, _provider_for_existing_job_cancel
from .test_api import api
from .test_migrations import migration_database
from .test_access_requests import legacy_records
from .test_legacy_inventory import fingerprints


@pytest.mark.parametrize("name", ["qwen", "seedream", "qwen_codex", "qwen_seedream"])
@pytest.mark.parametrize("transport", ["fixture", "mock", "codex"])
def test_retired_provider_cannot_be_injected_or_constructed(monkeypatch, name, transport):
    monkeypatch.setattr(get_settings(), "generation_provider", transport)
    with pytest.raises(RetiredProviderError):
        _provider_for_new_operation(SimpleNamespace(name=name))
    with pytest.raises(RetiredProviderError):
        get_provider_for_preset({"provider_preset": name})
    monkeypatch.setattr(get_settings(), "generation_provider", name)
    with pytest.raises(RetiredProviderError):
        get_generation_provider()


@pytest.mark.parametrize("name", ["qwen_codex", "qwen_seedream"])
def test_retired_configuration_and_cancellation_never_fall_back(tmp_path, monkeypatch, name):
    path = tmp_path / "active.json"
    content = json.dumps({"preset_id": name, "updated_by":"old-admin"})
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RetiredProviderError):
        ActiveProviderPresetStore(path).get()
    with pytest.raises(RetiredProviderError):
        ActiveProviderPresetStore(path).set(name, "admin")
    snapshot = snapshot_for_preset(resolve_preset(name))
    job = SimpleNamespace(provider=name, prompt_payload=snapshot)
    with pytest.raises(RetiredProviderError):
        _provider_for_existing_job_cancel(job)
    assert path.read_text(encoding="utf-8") == content and job.prompt_payload == snapshot


@pytest.mark.parametrize("provider,snapshot", [
    ("seedream", None), ("mock", {"analysis_provider":"qwen"}),
    ("codex", {"image_provider":"seedream"}), ("codex", {"provider_preset":"qwen_codex"}),
])
async def test_new_repository_write_refuses_before_schema_or_quota_work(monkeypatch, provider, snapshot):
    repository = JobRepository()
    async def forbidden():
        raise AssertionError("Retirement must be checked before touching the database")
    monkeypatch.setattr(repository, "_prepare", forbidden)
    with pytest.raises(RetiredProviderError):
        await repository.create(SimpleNamespace(), provider, "owner", snapshot)


def test_outbound_modules_credentials_and_active_options_removed():
    for name in ("qwen_provider", "seedream_provider"):
        assert importlib.util.find_spec("app.generation."+name) is None
    settings = Settings(_env_file=None)
    assert not any(name.startswith(("bailian_", "qwen_", "ark_", "seedream_")) for name in type(settings).model_fields)
    assert set(ACTIVE_PRESETS) == {"codex_full"}
    # No dependency removal is needed: retired providers used the shared HTTPX
    # dependency, still used by the application and object-capacity collector.


async def test_old_history_and_labels_are_readable_without_execution_modules(api):
    client, factory, owner, _, _ = api
    from app.db.models import GenerationJob
    _, identity, _, _, _ = await legacy_records(factory)
    async with factory.begin() as db:
        job = await db.get(GenerationJob, identity)
        job.user_id = owner
        job.provider = "qwen_seedream"
        job.prompt_payload = snapshot_for_preset(resolve_preset("qwen_seedream"))
    before = await fingerprints(factory)
    response = await client.get("/api/generation/jobs/"+identity)
    assert response.status_code == 200
    assert response.json()["provider"] == "qwen_seedream" and response.json()["outputs"]
    assert await fingerprints(factory) == before
    assert (await client.post("/api/generation/jobs/"+identity+"/cancel")).status_code == 503
    assert await fingerprints(factory) == before


def test_retired_global_setting_cannot_be_bypassed_by_codex_snapshot(monkeypatch):
    monkeypatch.setattr(get_settings(), "generation_provider", "seedream")
    with pytest.raises(RetiredProviderError):
        get_provider_for_preset(snapshot_for_preset(resolve_preset("codex_full")))


def test_queue_refuses_retired_provider_before_scheduling(monkeypatch):
    from app.generation.queue import GenerationQueue
    queue = GenerationQueue()
    def forbidden(*args):
        raise AssertionError("Retired provider must not enter the queue")
    monkeypatch.setattr(queue, "_queue_job", forbidden)
    with pytest.raises(RetiredProviderError):
        queue.submit_job("unchanged-job", SimpleNamespace(name="seedream"))
    assert not queue._tasks


def test_retired_snapshot_cannot_be_cancelled_using_a_fixture_label():
    snapshot = snapshot_for_preset(resolve_preset("qwen_seedream"))
    with pytest.raises(RetiredProviderError):
        _provider_for_existing_job_cancel(SimpleNamespace(provider="fixture", prompt_payload=snapshot))
