import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.generation.schemas import DEFAULT_LOCALE, Locale

_SAFE_ANALYSIS_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

DetailKind = Literal[
    "hair",
    "ears",
    "eyes",
    "expression",
    "headwear",
    "accessory",
    "requirement",
    "outfit",
    "color",
    "avoid",
    "other",
]

_DETAIL_KIND_VALUES = {
    "hair",
    "ears",
    "eyes",
    "expression",
    "headwear",
    "accessory",
    "requirement",
    "outfit",
    "color",
    "avoid",
    "other",
}

_HEAD_DETAIL_KIND_VALUES = {"hair", "ears", "eyes", "expression", "headwear", "accessory"}
USER_REQUIREMENT_FEATURE_ID = "feature-user-requirement"
_NON_HEAD_DETAIL_TERMS = (
    "hand",
    "hands",
    "gesture",
    "pose",
    "body",
    "clothing",
    "clothes",
    "outfit",
    "uniform",
    "dress",
    "shirt",
    "skirt",
    "wrist",
    "bracelet",
    "手",
    "手势",
    "动作",
    "姿势",
    "身体",
    "服装",
    "衣服",
    "制服",
    "裙",
    "上衣",
    "手腕",
    "手链",
)

_DETAIL_KIND_ALIASES: dict[str, str] = {
    "bangs": "hair",
    "bangs_reference": "hair",
    "clothes": "outfit",
    "clothing": "outfit",
    "clothing_reference": "outfit",
    "costume": "outfit",
    "ear": "ears",
    "ears": "ears",
    "eye": "eyes",
    "eye_reference": "eyes",
    "elf_ear": "ears",
    "elf_ears": "ears",
    "face": "other",
    "facial": "other",
    "animal_ear": "ears",
    "animal_ears": "ears",
    "beast_ear": "ears",
    "beast_ears": "ears",
    "cat_ear": "ears",
    "cat_ears": "ears",
    "head": "hair",
    "hair_accessory": "headwear",
    "hair_accessory_reference": "headwear",
    "hair_reference": "hair",
    "hair_shape": "hair",
    "hairstyle": "hair",
    "mouth": "expression",
    "horn": "ears",
    "horns": "ears",
    "pose": "other",
    "pose_reference": "other",
    "prop": "accessory",
    "props": "accessory",
    "requirement": "requirement",
    "requirements": "requirement",
    "render_style": "other",
    "style": "other",
    "user_note": "requirement",
    "user_requirement": "requirement",
    "user_requirements": "requirement",
    "wrist_accessory": "accessory",
    "アクセサリー": "accessory",
    "その他": "other",
    "口": "expression",
    "口元": "expression",
    "表情": "expression",
    "衣装": "outfit",
    "装飾": "accessory",
    "角": "ears",
    "顔": "other",
    "髪": "hair",
    "髪型": "hair",
    "耳": "ears",
    "目": "eyes",
    "眼": "eyes",
    "顔部": "other",
    "颜色": "color",
    "其它": "other",
    "其他": "other",
    "发型": "hair",
    "头发": "hair",
    "头部": "hair",
    "头脸": "other",
    "头饰": "headwear",
    "整体头脸": "hair",
    "嘴": "expression",
    "嘴巴": "expression",
    "脸": "other",
    "脸部": "other",
    "耳朵": "ears",
    "衣服": "outfit",
    "配饰": "accessory",
    "面部": "other",
    "眼睛": "eyes",
    "眼妆": "eyes",
    "眉毛": "expression",
}


class DetailFeature(BaseModel):
    id: str = Field(default_factory=lambda: f"feature-{uuid.uuid4().hex[:8]}")
    kind: DetailKind
    label: str = Field(default="", max_length=80)
    description: str = Field(max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DetailCropBbox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class DetailAnalysisProviderCrop(BaseModel):
    id: str = Field(default_factory=lambda: f"crop-{uuid.uuid4().hex[:8]}")
    kind: DetailKind
    description: str = Field(max_length=1000)
    source_reference_key: str
    bbox: dict[str, float]


class DetailAnalysisProviderRequest(BaseModel):
    analysis_id: str
    character_session_id: str
    free_text: str = ""
    locale: Locale = DEFAULT_LOCALE
    requirement_texts: list[str] = Field(default_factory=list)
    reference_keys: list[str] = Field(default_factory=list)
    reference_descriptions: list[dict[str, str]] = Field(default_factory=list)


class DetailAnalysisProviderResult(BaseModel):
    features: list[DetailFeature] = Field(default_factory=list)
    crops: list[DetailAnalysisProviderCrop] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DetailCropOut(BaseModel):
    id: str
    kind: DetailKind
    description: str
    source_reference_key: str
    bbox: dict[str, float]
    object_key: str
    image_url: str


def resolve_detail_bbox_pixels(
    bbox: dict[str, float],
    *,
    image_width: int,
    image_height: int,
    margin_ratio: float = 0.08,
) -> tuple[int, int, int, int]:
    x = float(bbox["x"])
    y = float(bbox["y"])
    width = float(bbox["width"])
    height = float(bbox["height"])

    if max(abs(x), abs(y), abs(width), abs(height)) <= 1:
        x *= image_width
        width *= image_width
        y *= image_height
        height *= image_height

    if width < 4 or height < 4:
        raise ValueError("bbox is too small")

    margin = max(width, height) * margin_ratio
    left = max(0, int(round(x - margin)))
    top = max(0, int(round(y - margin)))
    right = min(image_width, int(round(x + width + margin)))
    bottom = min(image_height, int(round(y + height + margin)))
    if right - left < 4 or bottom - top < 4:
        raise ValueError("bbox is too small")
    return left, top, right, bottom


def reference_key_to_relative_path(reference_key: str) -> Path | None:
    normalized = reference_key.replace("\\", "/")
    if "://" in normalized or normalized.startswith("//"):
        return None
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or len(posix_path.parts) < 3
        or posix_path.parts[0] != "references"
    ):
        return None
    return Path(*posix_path.parts[1:])


def _validate_analysis_id(analysis_id: str) -> str:
    if (
        analysis_id in {"", ".", ".."}
        or "/" in analysis_id
        or "\\" in analysis_id
        or PurePosixPath(analysis_id).is_absolute()
        or Path(analysis_id).is_absolute()
        or _SAFE_ANALYSIS_ID_PATTERN.fullmatch(analysis_id) is None
    ):
        raise ValueError("analysis_id is invalid")
    return analysis_id


def persist_detail_crops(
    *,
    analysis_id: str,
    provider_crops: list[DetailAnalysisProviderCrop],
    reference_root: Path,
    public_prefix: str,
    source_paths: Mapping[str, Path] | None = None,
) -> tuple[list[DetailCropOut], list[str]]:
    safe_analysis_id = _validate_analysis_id(analysis_id)
    output_dir = reference_root / safe_analysis_id
    output_dir.mkdir(parents=True, exist_ok=True)
    persisted: list[DetailCropOut] = []
    warnings: list[str] = []

    for crop in provider_crops:
        try:
            source_path = _source_path_for_crop(
                crop.source_reference_key,
                reference_root=reference_root,
                source_paths=source_paths,
            )
            with Image.open(source_path) as image:
                image = image.convert("RGB")
                box = resolve_detail_bbox_pixels(
                    crop.bbox,
                    image_width=image.width,
                    image_height=image.height,
                )
                cropped = image.crop(box)
                file_name = f"detail-{len(persisted) + 1}.webp"
                destination = output_dir / file_name
                cropped.save(destination, format="WEBP", quality=92)
        except (OSError, ValueError, KeyError, UnidentifiedImageError) as exc:
            warnings.append(f"Skipped crop {crop.id}: {exc}")
            continue

        object_key = f"references/{safe_analysis_id}/{file_name}"
        persisted.append(
            DetailCropOut(
                id=crop.id,
                kind=crop.kind,
                description=crop.description,
                source_reference_key=crop.source_reference_key,
                bbox=dict(crop.bbox),
                object_key=object_key,
                image_url=f"{public_prefix.rstrip('/')}/{object_key}",
            )
        )
    return persisted, warnings


def _source_path_for_crop(
    source_reference_key: str,
    *,
    reference_root: Path,
    source_paths: Mapping[str, Path] | None,
) -> Path:
    normalized_key = source_reference_key.strip()
    if source_paths:
        source_path = source_paths.get(normalized_key)
        if source_path is None and ":" in normalized_key:
            source_path = source_paths.get(normalized_key.split(":", 1)[1])
        if source_path is not None:
            resolved_source_path = source_path.resolve()
            resolved_source_path.relative_to(reference_root.resolve())
            return resolved_source_path

    relative_source = reference_key_to_relative_path(normalized_key)
    if relative_source is None:
        raise ValueError("source reference key is invalid")
    source_path = (reference_root / relative_source).resolve()
    source_path.relative_to(reference_root.resolve())
    return source_path


def parse_detail_analysis_json(text: str) -> DetailAnalysisProviderResult:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    payload: Any = json.loads(stripped)
    payload = _normalize_detail_analysis_payload(payload)
    return DetailAnalysisProviderResult.model_validate(payload)


def filter_head_detail_analysis_result(
    result: DetailAnalysisProviderResult,
) -> DetailAnalysisProviderResult:
    warnings = list(result.warnings)
    user_requirement_features: list[DetailFeature] = []
    features: list[DetailFeature] = []
    crops: list[DetailAnalysisProviderCrop] = []

    for feature in result.features:
        if _is_user_requirement_feature(feature):
            user_requirement_features.append(feature)
        elif _is_head_detail(feature.kind, feature.label, feature.description):
            features.append(feature)

    for crop in result.crops:
        if _is_head_detail(crop.kind, "", crop.description):
            crops.append(crop)

    return DetailAnalysisProviderResult(
        features=[*user_requirement_features[:1], *features],
        crops=crops,
        warnings=warnings,
    )


def ensure_user_requirement_feature(
    result: DetailAnalysisProviderResult,
    *,
    free_text: str,
    locale: str,
) -> DetailAnalysisProviderResult:
    description = free_text.strip()
    existing = [feature for feature in result.features if _is_user_requirement_feature(feature)]
    others = [feature for feature in result.features if not _is_user_requirement_feature(feature)]
    if existing:
        return DetailAnalysisProviderResult(
            features=[existing[0], *others],
            crops=result.crops,
            warnings=result.warnings,
        )
    if not description:
        return result
    return DetailAnalysisProviderResult(
        features=[
            DetailFeature(
                id=USER_REQUIREMENT_FEATURE_ID,
                kind="requirement",
                label=_user_requirement_label(locale),
                description=description[:1000],
            ),
            *others,
        ],
        crops=result.crops,
        warnings=result.warnings,
    )


def _is_head_detail(kind: str, label: str, description: str) -> bool:
    if kind not in _HEAD_DETAIL_KIND_VALUES:
        return False
    haystack = f"{label} {description}".lower()
    return not any(term in haystack for term in _NON_HEAD_DETAIL_TERMS)


def _is_user_requirement_feature(feature: DetailFeature) -> bool:
    return feature.id == USER_REQUIREMENT_FEATURE_ID or feature.kind == "requirement"


def _user_requirement_label(locale: str) -> str:
    if locale == "en":
        return "User requirement"
    if locale == "ja":
        return "ユーザー要望"
    return "用户要求"


def _normalize_detail_analysis_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    normalized["features"] = [
        _normalize_detail_feature(item)
        for item in normalized.get("features", [])
        if isinstance(item, dict)
    ]
    normalized["crops"] = [
        _normalize_detail_crop(item)
        for item in normalized.get("crops", [])
        if isinstance(item, dict)
    ]
    return normalized


def _normalize_detail_feature(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["kind"] = _normalize_detail_kind(normalized.get("kind"))
    return normalized


def _normalize_detail_crop(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["kind"] = _normalize_detail_kind(normalized.get("kind"))
    normalized["bbox"] = _normalize_detail_bbox(normalized.get("bbox"))
    return normalized


def _normalize_detail_kind(value: Any) -> str:
    if not isinstance(value, str):
        return "other"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _DETAIL_KIND_VALUES:
        return normalized
    if normalized in _DETAIL_KIND_ALIASES:
        return _DETAIL_KIND_ALIASES[normalized]
    if normalized.endswith("_reference"):
        normalized = normalized.removesuffix("_reference")
        if normalized in _DETAIL_KIND_ALIASES:
            return _DETAIL_KIND_ALIASES[normalized]
        if normalized in _DETAIL_KIND_VALUES:
            return normalized
    if "hair_accessory" in normalized or "hairclip" in normalized:
        return "headwear"
    if "ear" in normalized or "horn" in normalized:
        return "ears"
    if "hair" in normalized or "bang" in normalized:
        return "hair"
    if "eye" in normalized or "iris" in normalized:
        return "eyes"
    if "mouth" in normalized or "expression" in normalized:
        return "expression"
    if "accessory" in normalized or "clip" in normalized or "bracelet" in normalized:
        return "accessory"
    if "outfit" in normalized or "cloth" in normalized or "uniform" in normalized or "ribbon" in normalized:
        return "outfit"
    if "color" in normalized:
        return "color"
    return "other"


def _normalize_detail_bbox(value: Any) -> Any:
    if isinstance(value, dict):
        if {"x", "y", "width", "height"}.issubset(value.keys()):
            return value
        if {"x", "y", "w", "h"}.issubset(value.keys()):
            return {
                "x": value["x"],
                "y": value["y"],
                "width": value["w"],
                "height": value["h"],
            }
        if {"x1", "y1", "x2", "y2"}.issubset(value.keys()):
            x1 = float(value["x1"])
            y1 = float(value["y1"])
            return {
                "x": x1,
                "y": y1,
                "width": float(value["x2"]) - x1,
                "height": float(value["y2"]) - y1,
            }
        return value
    if not isinstance(value, list | tuple) or len(value) != 4:
        return value

    x, y, third, fourth = (float(part) for part in value)
    if third <= x or fourth <= y:
        width = third
        height = fourth
    else:
        width = third - x
        height = fourth - y
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }
