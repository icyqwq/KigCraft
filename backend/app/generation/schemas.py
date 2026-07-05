from typing import Literal

from pydantic import BaseModel, Field

from app.generation.modes import DEFAULT_GENERATION_MODE, GenerationMode


Locale = Literal["zh-CN", "en", "ja"]
DEFAULT_LOCALE: Locale = "zh-CN"
SUPPORTED_LOCALES: dict[str, Locale] = {
    "zh-CN": "zh-CN",
    "en": "en",
    "ja": "ja",
}


def normalize_locale(value: object) -> Locale:
    return SUPPORTED_LOCALES.get(value, DEFAULT_LOCALE) if isinstance(value, str) else DEFAULT_LOCALE


class ReferenceDescriptionIn(BaseModel):
    reference_key: str
    description: str = Field(default="", max_length=1000)


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


class DetailFeatureIn(BaseModel):
    id: str = Field(max_length=160)
    kind: DetailKind
    label: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=1000)


class DetailCropLockIn(BaseModel):
    reference_key: str = Field(max_length=300)
    kind: DetailKind
    description: str = Field(default="", max_length=1000)


class DetailLockIn(BaseModel):
    source_analysis_id: str | None = Field(default=None, max_length=160)
    user_note: str = Field(default="", max_length=2000)
    features: list[DetailFeatureIn] = Field(default_factory=list, max_length=24)
    crops: list[DetailCropLockIn] = Field(default_factory=list, max_length=24)


class CreateJobRequest(BaseModel):
    character_session_id: str | None = None
    free_text: str = Field(default="", max_length=2000)
    locale: str = Field(default=DEFAULT_LOCALE, max_length=16)
    requirement_ids: list[str] = Field(default_factory=list)
    reference_keys: list[str] = Field(default_factory=list)
    reference_descriptions: list[ReferenceDescriptionIn] = Field(default_factory=list)
    detail_lock: DetailLockIn | None = None
    generation_mode: GenerationMode = DEFAULT_GENERATION_MODE


class GenerationOutputOut(BaseModel):
    index: int
    object_key: str
    image_url: str
    width: int
    height: int
    landmarks: dict[str, dict[str, float]] | None = None


class TokenUsageOut(BaseModel):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None


class GenerationJobOut(BaseModel):
    id: str
    character_session_id: str
    generation_mode: GenerationMode = DEFAULT_GENERATION_MODE
    expected_output_count: int = 1
    status: str
    progress: int
    queue_position: int | None = None
    phase_label: str
    provider: str
    accepted_output_index: int | None = None
    token_usage: TokenUsageOut | None = None
    outputs: list[GenerationOutputOut] = Field(default_factory=list)


class GenerationEventOut(BaseModel):
    sequence: int
    type: str
    progress: int
    message: str
    created_at: str
    payload: dict[str, object] = Field(default_factory=dict)
