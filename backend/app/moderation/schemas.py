from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    verdict: Literal["allow", "reject"]
    # Why a request was rejected: the safety rules, or an attempt to steer the AI system (prompt injection).
    category: Literal["none", "unsafe", "injection"]
    reason: str = Field(max_length=600)


class AssetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    ordinal: int = Field(ge=1)
    verdict: Literal["allow", "reject", "uncertain"]


class CombinedReviewDecision(ReviewDecision):
    asset_decisions: list[AssetDecision]
