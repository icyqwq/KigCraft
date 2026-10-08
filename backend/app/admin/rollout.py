"""Database-backed account/design admission; never selects a legacy executor."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import delete, select, text
from app.admin.v2_controls import LOCK
from app.auth.models import UserRole
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction, Design, RuntimeControl
from app.design.service import ApplicationError, digest
from uuid import UUID

PREFIX = "roll:"  # Longest key is 48 characters, within RuntimeControl.scope(50).


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    restricted: StrictBool
    owners: dict[str, StrictBool] = Field(default_factory=dict, max_length=1000)
    designs: dict[str, StrictBool] = Field(default_factory=dict, max_length=1000)

    @field_validator("owners", "designs")
    @classmethod
    def canonical_ids(cls, values):
        for identity in values:
            if str(UUID(identity)) != identity:
                raise ValueError("Canonical UUID required")
        return values


async def read_policy(db):
    rows = (await db.scalars(select(RuntimeControl).where(
        RuntimeControl.scope.startswith(PREFIX)))).all()
    values = {row.scope: row.blocked for row in rows}
    # Missing policy preserves the existing local trial. Restrictions are explicit.
    return Policy(restricted=values.get(PREFIX+"restricted", False),
        owners={key.removeprefix(PREFIX+"owner:"): not value for key, value in values.items()
                if key.startswith(PREFIX+"owner:")},
        designs={key.removeprefix(PREFIX+"design:"): not value for key, value in values.items()
                 if key.startswith(PREFIX+"design:")})


def allows(policy, owner, design):
    account = policy.owners.get(owner)
    project = policy.designs.get(design)
    if account is False or project is False:
        return False
    return account is True or project is True or not policy.restricted


async def require_admission(db, owner, design):
    # Caller must authorize/lock the design first. Held through quote/submission commit.
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": LOCK})
    if not allows(await read_policy(db), owner, design):
        raise ApplicationError("rollout_not_enabled")


async def inspect(db):
    policy = (await read_policy(db)).model_dump(mode="json")
    return {"policy": policy, "digest": digest(policy)}


async def configure(db, actor_id, request_id, reason, expected_digest, policy):
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
        raise ApplicationError("invalid_request_id")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise ApplicationError("maintenance_reason_required")
    actor = await db.get(User, actor_id)
    if actor is None or actor.is_banned or actor.role != UserRole.admin:
        raise ApplicationError("admin_required")
    # Same request lock as other admin commands; same policy lock as submissions.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"),
                     {"value": "v2-admin:"+actor_id+":"+request_id})
    fingerprint = digest({"policy": policy.model_dump(mode="json"),
                          "expected_digest": expected_digest, "reason": reason.strip()})
    previous = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request_id))
    if previous:
        if previous.action != "configure_rollout" or previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        return {"receipt_id": previous.id, **previous.result}
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK})
    before = await inspect(db)
    if before["digest"] != expected_digest:
        raise ApplicationError("rollout_policy_conflict")
    owners = set((await db.scalars(select(User.id).where(User.id.in_(policy.owners)))).all())
    designs = set((await db.scalars(select(Design.id).where(Design.id.in_(policy.designs)))).all())
    if owners != set(policy.owners) or designs != set(policy.designs):
        raise ApplicationError("not_found")
    await db.execute(delete(RuntimeControl).where(RuntimeControl.scope.startswith(PREFIX)))
    db.add(RuntimeControl(scope=PREFIX+"restricted", blocked=policy.restricted))
    for kind, rules in (("owner", policy.owners), ("design", policy.designs)):
        db.add_all(RuntimeControl(scope=PREFIX+kind+":"+identity, blocked=not allowed)
                   for identity, allowed in rules.items())
    await db.flush()
    after = await inspect(db)
    now = utc_now()
    result = {"before": before, "after": after, "status": "completed"}
    receipt = AdminAction(actor_id=actor_id, client_request_id=request_id, request_digest=fingerprint,
        action="configure_rollout", target_id="global", reason=reason.strip(), status="completed",
        result=result, created_at=now, started_at=now, completed_at=now)
    db.add(receipt)
    await db.flush()
    return {"receipt_id": receipt.id, **result}
