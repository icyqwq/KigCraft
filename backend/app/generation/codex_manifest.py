import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from app.generation.provider import ProviderOutput


DEFAULT_WIDTH = 2048
DEFAULT_HEIGHT = 1536
FRONT_WIDTH = 800
FRONT_HEIGHT = 1100
TURNAROUND_WIDTH = 3000
TURNAROUND_HEIGHT = 2000
EXPECTED_INDEXES = [1, 2, 3, 4]
ALLOWED_IMAGE_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg"}


def parse_codex_manifest(
    manifest_path: Path,
    public_prefix: str,
    *,
    expected_indexes: list[int] | None = None,
    generation_mode: str | None = None,
    local_edit_expected: dict[str, int] | None = None,
) -> list[ProviderOutput]:
    expected_indexes = expected_indexes or EXPECTED_INDEXES
    default_width, default_height = _default_dimensions(generation_mode)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("generation_source") != "image_generation_tool":
        raise ValueError("Codex manifest must declare image generation tool source")
    if local_edit_expected is not None:
        _validate_local_edit_manifest(manifest, local_edit_expected)
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("Codex manifest must contain an outputs list")
    if len(outputs) != len(expected_indexes):
        raise ValueError(
            f"Codex manifest must contain exactly {len(expected_indexes)} outputs"
        )

    parsed_outputs = [
        _parse_output(
            output,
            manifest_path=manifest_path,
            public_prefix=public_prefix,
            default_width=default_width,
            default_height=default_height,
        )
        for output in outputs
    ]
    parsed_outputs.sort(key=lambda output: output.index)

    indexes = [output.index for output in parsed_outputs]
    if indexes != expected_indexes:
        raise ValueError("Codex manifest output indexes must match expected indexes")

    return parsed_outputs


def _validate_local_edit_manifest(manifest: dict[str, Any], expected: dict[str, int]) -> None:
    if manifest.get("tool_action") != "edit":
        raise ValueError("Codex local edit manifest must declare tool_action edit")
    if manifest.get("base_image") != "base.png":
        raise ValueError("Codex local edit manifest must declare base_image base.png")
    if manifest.get("mask_image") != "mask.png":
        raise ValueError("Codex local edit manifest must declare mask_image mask.png")
    width = expected.get("width")
    height = expected.get("height")
    outputs = manifest.get("outputs")
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict) and (output.get("width") != width or output.get("height") != height):
                raise ValueError("Codex local edit output must use the same dimensions as base.png")


def _parse_output(
    output: Any,
    *,
    manifest_path: Path,
    public_prefix: str,
    default_width: int,
    default_height: int,
) -> ProviderOutput:
    if not isinstance(output, dict):
        raise ValueError("Codex manifest outputs must be objects")

    index = output.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError("Codex manifest output index must be an integer")

    raw_path = output.get("path")
    if not isinstance(raw_path, str):
        raise ValueError("Codex manifest output path must be a string")
    relative_path = _normalize_relative_path(raw_path)

    width = output.get("width", default_width)
    height = output.get("height", default_height)
    if not isinstance(width, int) or isinstance(width, bool):
        raise ValueError("Codex manifest output width must be an integer")
    if not isinstance(height, int) or isinstance(height, bool):
        raise ValueError("Codex manifest output height must be an integer")

    prefix = public_prefix.rstrip("/")
    return ProviderOutput(
        index=index,
        object_key=f"codex/{manifest_path.parent.name}/{relative_path}",
        image_url=f"{prefix}/{relative_path}",
        width=width,
        height=height,
        landmarks=output.get("landmarks") if isinstance(output.get("landmarks"), dict) else None,
    )


def _default_dimensions(generation_mode: str | None) -> tuple[int, int]:
    if generation_mode == "turnaround":
        return TURNAROUND_WIDTH, TURNAROUND_HEIGHT
    if generation_mode in {"front_design", "front_revision", "front_local_revision"}:
        return FRONT_WIDTH, FRONT_HEIGHT
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def _normalize_relative_path(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/")
    if not normalized:
        raise ValueError("Codex manifest output path cannot be empty")
    if PurePosixPath(normalized).is_absolute():
        raise ValueError("Codex manifest output path cannot be absolute")

    windows_path = PureWindowsPath(raw_path)
    if windows_path.is_absolute() or windows_path.drive:
        raise ValueError("Codex manifest output path cannot be absolute")

    parts = PurePosixPath(normalized).parts
    if any(part == ".." for part in parts):
        raise ValueError("Codex manifest output path cannot escape its workspace")
    if not parts or parts[0] != "outputs":
        raise ValueError("Codex manifest output path must be under outputs/")
    if PurePosixPath(normalized).suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Codex manifest output path must be an image file")

    return str(PurePosixPath(*parts))

