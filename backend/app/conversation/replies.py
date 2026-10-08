"""Versioned final-reply classification; it cannot authorize images or billing."""
from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from app.conversation.public_text import public_assistant_prefix, public_assistant_text

IMAGE_KINDS = ("head_shell", "reference")
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "enum": [2]},
        "kind": {"type": "string", "enum": ["question", "response"]},
        "text": {"type": "string"},
        # Required-but-nullable keeps the schema valid for strict structured output; the parser also accepts it missing.
        "image_kind": {"type": ["string", "null"], "enum": [*IMAGE_KINDS, None]},
    },
    "required": ["schema_version", "kind", "text", "image_kind"],
    "additionalProperties": False,
}
REPLY_INSTRUCTIONS = (
    "Return your final user-facing reply in the supplied JSON schema, schema_version 2. "
    "Set kind=question ONLY when you are actually asking the user for missing information "
    "and must wait for their answer; text must contain that concise question. "
    "Use kind=response for acknowledgments, explanations, refusals and delivery confirmations, "
    "including quoted or rhetorical questions. Do not invent a question just to fill the schema. "
    "The text field contains only the reply in the user's language. "
    "When this turn generated an image, set image_kind to head_shell for a finished head-shell photo "
    "(shell, wig, studio shot) or reference for character art used only as a reference; "
    "otherwise set image_kind to null. "
    "This classification does not grant tool permission, change the authorized scope or prove image delivery."
)

class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1, 2]
    kind: Literal["question", "response"]
    text: StrictStr = Field(min_length=1, max_length=16000)
    # Missing (v1, or no image this turn) is treated as head_shell by readers that need a value.
    image_kind: Literal["head_shell", "reference"] | None = None

    @model_validator(mode="after")
    def _version_fields(self):
        if self.schema_version == 1 and self.image_kind is not None:
            raise ValueError("image_kind requires reply schema_version 2")
        return self

def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate reply field")
        value[key] = item
    return value

def _structured(value: str) -> bool:
    return value.lstrip().startswith(("{", "```"))

def message_parts(item: dict) -> list[dict]:
    """Keep legacy plain replies readable, but never infer a question from punctuation."""
    raw = item["text"]
    if not _structured(raw):
        text = public_assistant_text(raw)
        parts = [{"type": "text", "text": text}] if text else []
        if parts and item.get("phase") == "commentary":
            parts.append({"type": "reply_intent", "schema_version": 1, "kind": "commentary"})
        return parts
    try:
        if item.get("phase") not in (None, "final_answer") or len(raw) > 64000:
            raise ValueError("Invalid final reply")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
            raise ValueError("Invalid reply version")
        reply = Reply.model_validate(value)
        text = public_assistant_text(reply.text)
        if not text:
            raise ValueError("Empty public reply")
    except (ValueError, TypeError):
        # Retain an internal failure marker so an earlier commentary cannot become a final reply.
        return [{"type": "reply_intent", "schema_version": 1, "kind": "invalid"}]
    # The marker keeps its own version 1; image_kind is added only when the model declared one.
    marker = {"type": "reply_intent", "schema_version": 1, "kind": reply.kind,
              **({"image_kind": reply.image_kind} if reply.image_kind else {})}
    return [{"type": "text", "text": text}, marker]

def message_prefix(raw: str) -> str:
    # The final JSON envelope is validated atomically. Commentary still streams normally.
    return "" if _structured(raw) else public_assistant_prefix(raw)

def message_kind(parts: list[dict]) -> str | None:
    markers = [part for part in parts if part.get("type") == "reply_intent"]
    if markers:
        if len(markers) != 1 or markers[0].get("schema_version") != 1:
            return "invalid"
        kind = markers[0].get("kind")
        if kind == "commentary":
            return None
        return kind if kind in {"question", "response", "invalid"} else "invalid"
    return "response" if any(part.get("type") == "text" and
        public_assistant_text(part.get("text", "")) for part in parts) else None

def message_image_kind(parts: list[dict]) -> str | None:
    """The image kind a final reply declared, or None when it declared nothing valid."""
    markers = [part for part in parts if part.get("type") == "reply_intent"]
    if len(markers) != 1 or markers[0].get("schema_version") != 1:
        return None
    kind = markers[0].get("image_kind")
    return kind if kind in IMAGE_KINDS else None
