"""Which kind of image a generated version is. Revisions are immutable, so this is decided at insert time."""
from __future__ import annotations
from app.conversation.questions import completed_messages
from app.conversation.replies import message_image_kind, message_kind


async def resolve_image_kind(db, operation, source_revision) -> str:
    """Kind for the version being published.

    1. What the final structured reply declared (reply schema_version 2).
    2. Otherwise a local edit or regeneration keeps the kind of the image it started from.
    3. Otherwise head_shell, the default deliverable (a missing declaration, or a v1 reply).

    The worker publishes only after the turn ends, so the final reply is already stored here.
    """
    if operation.kind == "viewset":
        return "head_shell"
    # An explicit choice made when the turn was sent (flat reference step, or "make the head shell") wins.
    from app.contracts.v2 import InputSnapshot
    from app.design.models import InputRecord
    record = await db.get(InputRecord, operation.input_snapshot_id)
    if record is not None:
        chosen = InputSnapshot.model_validate(record.snapshot).target_kind
        if chosen:
            return "head_shell" if chosen == "four_view" else chosen
    for row in reversed(await completed_messages(db, operation)):
        if message_kind(row.content_parts) is not None:
            declared = message_image_kind(row.content_parts)
            if declared:
                return declared
            break
    if operation.kind in {"local_edit", "regenerate"} and source_revision is not None:
        return source_revision.image_kind
    return "head_shell"
