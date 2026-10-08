"""Message-local labels derived only from immutable input order."""
from __future__ import annotations
import copy
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.contracts.v2 import InputSnapshot

def reference_bindings(snapshot: InputSnapshot) -> list[dict]:
    return [{"asset_id": item.asset_id, "usage": item.usage, "number": number}
            for number, item in enumerate((r for r in snapshot.references if r.role == "reference"), 1)]

def annotation_bindings(snapshot: InputSnapshot) -> list[dict]:
    marks = snapshot.prepared_edit.recipe.get("annotations", []) if snapshot.prepared_edit else []
    if not isinstance(marks, list):
        return []
    # Old partial recipes are data, not a reason to invent missing identities or renumber later marks.
    return [{"annotation_id": mark["id"], "number": number,
             "note": mark.get("note", "") if isinstance(mark.get("note", ""), str) else "",
             "text": mark.get("text", "") if isinstance(mark.get("text", ""), str) else ""}
            for number, mark in enumerate(marks, 1)
            if isinstance(mark, dict) and isinstance(mark.get("id"), str) and mark["id"]]

def user_content_parts(snapshot: InputSnapshot) -> list[dict]:
    parts = [{"type": "text", "text": snapshot.user_text},
             {"type": "references", "items": reference_bindings(snapshot)}]
    annotations = annotation_bindings(snapshot)
    if annotations:
        parts.append({"type": "annotations", "annotations": annotations})
    return parts

def public_numbered_parts(parts: list[dict], snapshot: InputSnapshot | None = None) -> list[dict]:
    result = copy.deepcopy(parts)
    # Older messages already froze reference array order; do not consult the current composer.
    for part in result:
        if part.get("type") == "references":
            for number, item in enumerate(part.get("items", []), 1):
                item.setdefault("number", number)
    if snapshot and not any(part.get("type") == "annotations" for part in result):
        annotations = annotation_bindings(snapshot)
        if annotations:
            result.append({"type": "annotations", "annotations": annotations})
    return result
