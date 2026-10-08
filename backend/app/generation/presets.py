import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.core.time import utc_now
from app.generation.retired import reject_retired


ProviderPresetId = Literal["codex_full", "qwen_codex", "qwen_seedream"]


@dataclass(frozen=True)
class ProviderPreset:
    id: ProviderPresetId
    label: str
    analysis_provider: str
    analysis_model: str
    image_provider: str
    image_model: str


PRESETS: dict[ProviderPresetId, ProviderPreset] = {
    "codex_full": ProviderPreset(
        "codex_full", "Codex 全链路", "codex", "gpt-6-astra", "codex", "gpt-6-astra"
    ),
    "qwen_codex": ProviderPreset(
        "qwen_codex", "Qwen + Codex", "qwen", "qwen3.7-plus", "codex", "gpt-6-astra"
    ),
    "qwen_seedream": ProviderPreset(
        "qwen_seedream",
        "Qwen + Seedream",
        "qwen",
        "qwen3.7-plus",
        "seedream",
        "Seedream 5.0 Pro",
    ),
}


ACTIVE_PRESETS = {key: value for key, value in PRESETS.items() if key == "codex_full"}

@dataclass(frozen=True)
class ActiveProviderPreset:
    preset_id: ProviderPresetId
    updated_by: str = ""
    updated_at: str = ""


def resolve_preset(preset_id: str) -> ProviderPreset:
    try:
        return PRESETS[preset_id]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider preset: {preset_id}") from exc


def snapshot_for_preset(preset: ProviderPreset) -> dict[str, str]:
    return {
        "provider_preset": preset.id,
        "analysis_provider": preset.analysis_provider,
        "analysis_model": preset.analysis_model,
        "image_provider": preset.image_provider,
        "image_model": preset.image_model,
    }


class ActiveProviderPresetStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self) -> ActiveProviderPreset:
        if not self._path.is_file():
            return ActiveProviderPreset(preset_id="codex_full")

        payload = json.loads(self._path.read_text(encoding="utf-8"))
        reject_retired(str(payload["preset_id"]))
        preset = resolve_preset(str(payload["preset_id"]))
        return ActiveProviderPreset(
            preset_id=preset.id,
            updated_by=str(payload.get("updated_by", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def set(self, preset_id: ProviderPresetId, updated_by: str) -> ActiveProviderPreset:
        reject_retired(preset_id)
        preset = resolve_preset(preset_id)
        active_preset = ActiveProviderPreset(
            preset_id=preset.id,
            updated_by=updated_by,
            updated_at=utc_now().isoformat(),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f"{self._path.name}.{uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(
                    {
                        "preset_id": active_preset.preset_id,
                        "updated_by": active_preset.updated_by,
                        "updated_at": active_preset.updated_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temp_path.replace(self._path)
        finally:
            temp_path.unlink(missing_ok=True)
        return active_preset
