import pytest

from app.generation.presets import (
    ActiveProviderPresetStore,
    PRESETS,
    snapshot_for_preset,
)


def test_active_preset_defaults_and_persists(tmp_path):
    store = ActiveProviderPresetStore(tmp_path / "provider-preset.json")
    assert store.get().preset_id == "codex_full"

    saved = store.set("codex_full", updated_by="admin-1")
    assert saved.preset_id == "codex_full"
    assert ActiveProviderPresetStore(tmp_path / "provider-preset.json").get() == saved


def test_unknown_preset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsupported provider preset"):
        ActiveProviderPresetStore(tmp_path / "provider-preset.json").set(
            "qwen_other", updated_by="admin-1"
        )


def test_historical_qwen_codex_snapshot_remains_readable():
    assert snapshot_for_preset(PRESETS["qwen_codex"]) == {
        "provider_preset": "qwen_codex",
        "analysis_provider": "qwen",
        "analysis_model": "qwen3.7-plus",
        "image_provider": "codex",
        "image_model": "gpt-6-astra",
    }


def test_snapshot_uses_the_selected_preset_values():
    assert snapshot_for_preset(PRESETS["qwen_seedream"]) == {
        "provider_preset": "qwen_seedream",
        "analysis_provider": "qwen",
        "analysis_model": "qwen3.7-plus",
        "image_provider": "seedream",
        "image_model": "Seedream 5.0 Pro",
    }
