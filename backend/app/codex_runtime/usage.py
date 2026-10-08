"""Whitelisted upstream snapshots, not billing totals or per-turn usage."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_COUNT = 9007199254740991

class Breakdown(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    inputTokens: int = Field(ge=0, le=MAX_COUNT)
    cachedInputTokens: int = Field(ge=0, le=MAX_COUNT)
    outputTokens: int = Field(ge=0, le=MAX_COUNT)
    reasoningOutputTokens: int = Field(ge=0, le=MAX_COUNT)
    totalTokens: int = Field(ge=0, le=MAX_COUNT)
    cacheWriteInputTokens: int | None = Field(default=None, ge=0, le=MAX_COUNT)

class UsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    last: Breakdown
    total: Breakdown
    modelContextWindow: int | None = Field(default=None, ge=1, le=MAX_COUNT)

def token_snapshot(value: Any) -> dict:
    try:
        usage = UsageSnapshot.model_validate(value)
    except ValidationError:
        return {"status": "invalid", "snapshot": None}
    return {"status": "observed", "snapshot": usage.model_dump()}
