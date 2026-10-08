"""Permanent deletion of one design: its conversation, versions, drafts, four-view groups and images.

There is no trash. Money records survive the design: ledger rows keep their amounts and owner and only lose
the link to the deleted operation. Quotes and audit actions are not design content and stay; review-cache rows are dropped with their operation (they only save a re-review).
The immutability guards are lifted for this one transaction by a transaction-local setting that only this
module sets (see migration v2_0029); every other writer is still refused.
"""
from __future__ import annotations
import logging
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, text
from app.core.config import get_settings
from app.design.models import Design, Operation
from app.design.service import ApplicationError
from app.operations.service import ACTIVE, TERMINAL

log = logging.getLogger(__name__)
PURGE_SETTING = "kig.v2_design_purge"


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_metadata_version: int = Field(ge=0)


# Assets a design may reference, by the rows that are about to disappear. JSON references are read as data.
CANDIDATE_ASSETS = text("""
SELECT DISTINCT id FROM (
  SELECT clean_asset_id AS id FROM v2_revisions WHERE design_id = :d
  UNION SELECT root_source_asset_id FROM v2_revisions WHERE design_id = :d
  UNION SELECT root_asset_id FROM v2_editor_checkpoints WHERE design_id = :d
  UNION SELECT clean_asset_id FROM v2_editor_checkpoints WHERE design_id = :d
  UNION SELECT root_asset_id FROM v2_editor_drafts WHERE design_id = :d
  UNION SELECT e ->> 'asset_id' FROM v2_input_snapshots s,
        jsonb_array_elements(coalesce(s.snapshot::jsonb -> 'references', '[]'::jsonb)) e
        WHERE s.branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)
  UNION SELECT s.snapshot::jsonb -> 'prepared_edit' ->> 'clean_asset_id' FROM v2_input_snapshots s
        WHERE s.branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)
  UNION SELECT s.snapshot::jsonb -> 'prepared_edit' ->> 'root_source_asset_id' FROM v2_input_snapshots s
        WHERE s.branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)
  UNION SELECT i ->> 'asset_id' FROM v2_messages m,
        jsonb_array_elements(coalesce(m.content_parts::jsonb, '[]'::jsonb)) p,
        jsonb_array_elements(CASE WHEN jsonb_typeof(p -> 'items') = 'array' THEN p -> 'items' ELSE '[]'::jsonb END) i
        WHERE m.branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)
) found WHERE id IS NOT NULL
""")

STEPS = [
    "UPDATE v2_designs SET last_branch_id = NULL WHERE id = :d",
    "UPDATE v2_branches SET current_revision_id = NULL, origin_revision_id = NULL, parent_branch_id = NULL, active_epoch_id = NULL WHERE design_id = :d",
    "UPDATE v2_ledger SET operation_id = NULL WHERE operation_id IN (SELECT id FROM v2_operations WHERE design_id = :d)",
    # source_operation_id is NOT NULL: the cached verdict is only a cache, so it goes with the operation that produced it.
    "DELETE FROM v2_asset_review_cache WHERE source_operation_id IN (SELECT id FROM v2_operations WHERE design_id = :d)",
    "DELETE FROM v2_editor_save_receipts WHERE design_id = :d",
    "DELETE FROM v2_editor_checkpoints WHERE design_id = :d",
    "DELETE FROM v2_draft_write_receipts WHERE draft_id IN (SELECT id FROM v2_editor_drafts WHERE design_id = :d)",
    "DELETE FROM v2_editor_drafts WHERE design_id = :d",
    "DELETE FROM v2_viewset_attempts WHERE design_id = :d",
    "DELETE FROM v2_viewset_items WHERE design_id = :d",
    "DELETE FROM v2_viewsets WHERE design_id = :d",
    "DELETE FROM v2_legacy_imports WHERE design_id = :d",
    "DELETE FROM v2_recovery_checks WHERE operation_id IN (SELECT id FROM v2_operations WHERE design_id = :d)",
    "DELETE FROM v2_context_observations WHERE operation_id IN (SELECT id FROM v2_operations WHERE design_id = :d)",
    "DELETE FROM v2_runtime_observations WHERE operation_id IN (SELECT id FROM v2_operations WHERE design_id = :d)",
    "DELETE FROM v2_messages WHERE branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)",
    "DELETE FROM v2_revisions WHERE design_id = :d",
    "DELETE FROM v2_operations WHERE design_id = :d",
    "DELETE FROM v2_input_snapshots WHERE branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)",
    "DELETE FROM v2_codex_epochs WHERE branch_id IN (SELECT id FROM v2_branches WHERE design_id = :d)",
    "DELETE FROM v2_events WHERE design_id = :d",
    "DELETE FROM v2_branches WHERE design_id = :d",
    "DELETE FROM v2_designs WHERE id = :d",
]

# An asset goes only when nothing that remains still points at it: foreign keys first, then JSON references.
DELETE_UNREFERENCED = text("""
DELETE FROM v2_assets a WHERE a.id = ANY(:ids) AND a.owner_id = :o
  AND NOT EXISTS (SELECT 1 FROM v2_revisions r WHERE r.clean_asset_id = a.id OR r.root_source_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM v2_editor_checkpoints c WHERE c.root_asset_id = a.id OR c.clean_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM v2_editor_drafts d WHERE d.root_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM v2_legacy_materials l WHERE l.asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM v2_input_snapshots s WHERE strpos(s.snapshot::text, a.id) > 0)
  AND NOT EXISTS (SELECT 1 FROM v2_messages m WHERE strpos(m.content_parts::text, a.id) > 0)
  AND NOT EXISTS (SELECT 1 FROM v2_viewsets v WHERE strpos(v.snapshot::text, a.id) > 0)
RETURNING a.object_key
""")


async def purge_design(db, owner: str, design_id: str, request: DeleteRequest) -> list[str]:
    """Delete the design inside the caller's transaction; return the storage keys to remove after it commits."""
    design = await db.scalar(select(Design).where(Design.id == design_id, Design.owner_id == owner).with_for_update())
    if design is None:
        raise ApplicationError("not_found")
    if design.metadata_version != request.expected_metadata_version:
        raise ApplicationError("metadata_conflict")
    if await db.scalar(select(Operation.id).where(Operation.design_id == design.id, Operation.state.in_(ACTIVE)).limit(1)):
        raise ApplicationError("design_busy")
    # Never remove a design while upstream completion or settlement is still uncertain.
    if await db.scalar(select(Operation.id).where(Operation.design_id == design.id, or_(
        Operation.state.not_in(TERMINAL),
        and_(Operation.dispatch_intent_at.is_not(None), or_(Operation.upstream_status.is_(None),
             Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
        and_(Operation.reserved_units > 0, Operation.reservation_closed.is_(False)))).limit(1)):
        raise ApplicationError("design_delete_unconfirmed")
    params = {"d": design.id, "o": owner}
    candidates = [row[0] for row in (await db.execute(CANDIDATE_ASSETS, params)).all()]
    await db.execute(text("SELECT set_config(:name, 'on', true)"), {"name": PURGE_SETTING})
    for statement in STEPS:
        await db.execute(text(statement), params)
    keys = []
    if candidates:
        keys = [row[0] for row in (await db.execute(DELETE_UNREFERENCED, {"ids": candidates, "o": owner})).all()]
    # Transaction-local anyway; switch it off at once so nothing later in this transaction runs unguarded.
    await db.execute(text("SELECT set_config(:name, 'off', true)"), {"name": PURGE_SETTING})
    return keys


def remove_objects(keys: list[str]) -> None:
    """Best effort after the commit; anything left behind is an orphan the orphan-asset cleanup collects."""
    from app.assets.repository import asset_store
    try:
        store = asset_store(settings=get_settings())
    except ApplicationError:
        log.warning("design purge: asset storage unavailable, %d objects left for orphan cleanup", len(keys))
        return
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            log.warning("design purge: could not remove one object; orphan cleanup will collect it")
