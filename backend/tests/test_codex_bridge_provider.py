import pytest

from app.core.config import get_settings
from app.generation import provider as provider_module


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def enable_bridge_provider(monkeypatch):
    monkeypatch.setenv("GENERATION_PROVIDER", "codex_bridge")
    monkeypatch.setenv("CODEX_BRIDGE_URL", "http://bridge.local")
    monkeypatch.setenv("CODEX_BRIDGE_TOKEN", "test-token")
    monkeypatch.setenv("CODEX_BRIDGE_TIMEOUT_SECONDS", "12")
    get_settings.cache_clear()


async def test_codex_bridge_provider_posts_sanitized_job_to_bridge(monkeypatch):
    enable_bridge_provider(monkeypatch)
    calls = []

    async def fake_post(settings, payload):
        calls.append((settings, payload))
        index = payload["output_index"]
        return {
            "outputs": [
                {
                    "index": index,
                    "object_key": f"codex/session-a/job-1/outputs/candidate-{index}.webp",
                    "image_url": f"/api/generated/session-a/job-1/outputs/candidate-{index}.webp",
                    "width": 800,
                    "height": 800,
                    "landmarks": {
                        "leftEye": {"x": 0.42, "y": 0.4},
                        "rightEye": {"x": 0.58, "y": 0.4},
                        "chin": {"x": 0.5, "y": 0.7},
                        "jawLeft": {"x": 0.38, "y": 0.6},
                        "jawRight": {"x": 0.62, "y": 0.6},
                    },
                }
            ]
        }

    monkeypatch.setattr(provider_module, "_post_codex_bridge_candidate", fake_post, raising=False)

    provider = provider_module.get_generation_provider()
    outputs = await provider.generate(
        "job-1",
        {
            "character_session_id": "session-a",
            "reference_keys": ["front:references/upload-1/front.webp"],
            "system_constraints": ["fixed system guardrail"],
            "user_requirements": ["soft youthful expression"],
            "user_notes": "keep green hair tips",
        },
    )

    assert provider.name == "codex_bridge"
    assert [output.index for output in outputs] == [1]
    assert outputs[0].image_url == "/api/generated/session-a/job-1/outputs/candidate-1.webp"
    assert len(calls) == 1

    settings, payload = calls[0]
    assert settings.codex_bridge_url == "http://bridge.local"
    assert settings.codex_bridge_token == "test-token"
    assert payload["job_id"] == "job-1"
    assert payload["character_session_id"] == "session-a"
    assert payload["output_index"] == 1
    assert payload["reference_keys"] == ["front:references/upload-1/front.webp"]
    assert "gpt-image-2" in payload["prompt_text"]
    assert "fixed system guardrail" in payload["prompt_text"]
    assert "soft youthful expression" in payload["prompt_text"]
    assert "keep green hair tips" in payload["prompt_text"]
    assert "front-view design preview" in payload["prompt_text"]
    assert "Produce exactly one image for candidate 1" in payload["prompt_text"]
    assert "stand" not in payload["prompt_text"].lower()


async def test_codex_bridge_provider_rejects_wrong_candidate_response_count(monkeypatch):
    enable_bridge_provider(monkeypatch)

    async def fake_post(settings, payload):
        return {
            "outputs": [
                {
                    "index": 1,
                    "object_key": "codex/session-a/job-1/outputs/candidate-1.webp",
                    "image_url": "/api/generated/session-a/job-1/outputs/candidate-1.webp",
                },
                {
                    "index": 1,
                    "object_key": "codex/session-a/job-1/outputs/candidate-1-copy.webp",
                    "image_url": "/api/generated/session-a/job-1/outputs/candidate-1-copy.webp",
                }
            ]
        }

    monkeypatch.setattr(provider_module, "_post_codex_bridge_candidate", fake_post, raising=False)

    provider = provider_module.get_generation_provider()

    with pytest.raises(RuntimeError, match="exactly 1"):
        await provider.generate(
            "job-1",
            {
                "character_session_id": "session-a",
                "reference_keys": ["front:references/upload-1/front.webp"],
            },
        )


def test_get_generation_provider_supports_codex_bridge(monkeypatch):
    enable_bridge_provider(monkeypatch)

    provider = provider_module.get_generation_provider()

    assert provider.name == "codex_bridge"


def test_codex_prompt_requires_single_front_design_image():
    prompt_text = provider_module._build_codex_prompt(
        {
            "character_session_id": "session-a",
            "reference_keys": ["front:references/upload-1/front.webp"],
            "system_constraints": ["fixed system guardrail"],
            "user_requirements": ["soft youthful expression"],
            "user_notes": "keep blue hair",
        }
    )

    assert "front-view design preview" in prompt_text
    assert "Produce exactly one image" in prompt_text
    assert "outputs/candidate-1.webp" in prompt_text
    assert "candidate-2.webp" not in prompt_text
    assert "800x1100" in prompt_text
    assert "stand" not in prompt_text.lower()
    assert "long loose hair must remain continuous" in prompt_text


def test_codex_prompt_supports_single_front_design_stage():
    prompt_text = provider_module._build_codex_prompt(
        {
            "character_session_id": "session-a",
            "generation_mode": "front_design",
            "expected_output_count": 1,
            "reference_keys": ["front:references/upload-1/front.webp"],
            "system_constraints": ["front stage guardrail"],
            "user_requirements": ["soft youthful expression"],
            "user_notes": "keep blue hair",
        }
    )

    assert "front-view design preview" in prompt_text
    assert "finished-product reference image" in prompt_text
    assert "only for physical kigurumi head shell product-photo qualities" in prompt_text
    assert "Do not copy the fixed reference character design" in prompt_text
    assert "Produce exactly one image" in prompt_text
    assert "outputs/candidate-1.webp" in prompt_text
    assert "candidate-2.webp" not in prompt_text
    assert "Output only one front-view design image" in prompt_text
    assert "800x1100" in prompt_text
    assert "stand" not in prompt_text.lower()
    assert "long loose hair must remain continuous" in prompt_text


def test_codex_prompt_does_not_request_stands_for_turnaround():
    prompt_text = provider_module._build_codex_prompt(
        {
            "character_session_id": "session-a",
            "generation_mode": "turnaround",
            "expected_output_count": 1,
            "reference_keys": ["front:references/upload-1/front.webp"],
            "system_constraints": ["front stage guardrail"],
            "user_requirements": ["soft youthful expression"],
            "user_notes": "keep blue hair",
        }
    )

    assert "stand" not in prompt_text.lower()
    assert "long loose hair must stay continuous" in prompt_text
