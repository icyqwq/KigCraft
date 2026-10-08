from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.time import utc_now
from app.design.service import ApplicationError, event, owned_design


class UpdateDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_metadata_version: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def changes_required(self):
        fields = self.model_fields_set - {"expected_metadata_version"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("At least one non-null change is required")
        if self.title is not None and not self.title.strip():
            raise ValueError("Title cannot be blank")
        return self


async def update_design(db, owner, design_id, payload):
    design = await owned_design(db, owner, design_id, lock=True)
    if design.metadata_version != payload.expected_metadata_version:
        raise ApplicationError("metadata_conflict")
    changes = {}
    if payload.title is not None and payload.title != design.title:
        changes["title"] = payload.title
    if payload.pinned is not None and payload.pinned != design.pinned:
        changes["pinned"] = payload.pinned
    if payload.archived is not None and payload.archived != (design.archived_at is not None):
        changes["archived_at"] = utc_now() if payload.archived else None
    if changes:
        for key, value in changes.items():
            setattr(design, key, value)
        design.metadata_version += 1
        await event(db, design.id, "session.updated", {"design_id":design.id})
    return design
