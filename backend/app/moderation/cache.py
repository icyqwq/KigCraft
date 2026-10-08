"""Stable image assessments only. Every operation still requires a semantic review."""
import hashlib
import json
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.contracts.v2 import InputSnapshot
from app.core.time import utc_now
from app.design.models import Asset, AssetReviewCache
from app.design.service import ApplicationError
from app.moderation.reviewer import REVIEW_INSTRUCTIONS, ASSET_INSTRUCTIONS
from app.moderation.schemas import CombinedReviewDecision


def policy_key(*, model: str, codex_version: str, runtime_identity: str) -> str:
    # Prompt/schema edits and runtime/model changes invalidate earlier stable verdicts.
    value = {"version": 1, "rules": REVIEW_INSTRUCTIONS, "asset_rules": ASSET_INSTRUCTIONS,
             "schema": CombinedReviewDecision.model_json_schema(), "model": model,
             "codex_version": codex_version, "runtime_identity": runtime_identity}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def cached_assets(db, owner: str, snapshot: InputSnapshot, policy: str) -> dict[str, str]:
    hashes = {ref.sha256 for ref in snapshot.references}
    if not hashes:
        return {}
    # A cache hit never grants asset access, even if callers already ran prepare().
    for ref in snapshot.references:
        asset = await db.get(Asset, ref.asset_id)
        if asset is None or asset.owner_id != owner or asset.status != "ready" or asset.sha256 != ref.sha256:
            raise ApplicationError("input_asset_unavailable")
    rows = await db.scalars(select(AssetReviewCache).where(
        AssetReviewCache.owner_id == owner, AssetReviewCache.sha256.in_(hashes),
        AssetReviewCache.policy_key == policy))
    return {row.sha256: row.verdict for row in rows}


def missing_assets(snapshot: InputSnapshot, cached: dict[str, str]) -> dict[int, str]:
    missing = {}
    seen = set(cached)
    for ref in snapshot.references:
        if ref.sha256 not in seen:
            missing[ref.ordinal] = ref.sha256
            seen.add(ref.sha256)
    return missing


async def save_assets(db, *, owner: str, operation_id: str, policy: str,
                      missing: dict[int, str], decision: CombinedReviewDecision):
    # Called only after exact ordinal/schema validation and under the operation fence.
    for asset in decision.asset_decisions:
        if asset.verdict == "uncertain":
            continue
        await db.execute(insert(AssetReviewCache).values(
            owner_id=owner, sha256=missing[asset.ordinal], policy_key=policy,
            verdict=asset.verdict, source_operation_id=operation_id, created_at=utc_now()
        ).on_conflict_do_nothing(index_elements=["owner_id", "sha256", "policy_key"]))
