"""Persisted partial messages survive reconnects without becoming final replies."""
import pytest
from sqlalchemy import select
from app.design.models import Message, DomainEvent, Design, Wallet
from app.design.service import ApplicationError
from app.operations.lifecycle import message_delta, message_completed, turn_started, finish
from .test_operations import factory, setup, quoted, submit_request, running
from .test_migrations import migration_database


async def prepare(factory):
    owner, design, branch = await setup(factory)
    opid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, opid)
    async with factory.begin() as db:
        await turn_started(db, opid, "worker", token, "turn")
    return owner, design, branch, opid, token


async def test_partial_replace_duplicate_final_and_private_fields(factory):
    owner, design, branch, opid, token = await prepare(factory)
    async with factory.begin() as db:
        first = await message_delta(db, opid, "worker", token, "turn",
                                    {"id":"item", "text":"Coral wig looks ", "reasoning":"private"})
        identity, sequence = first.id, first.history_sequence
    async with factory.begin() as db:
        before = (await db.get(Design, design)).last_activity_at
        await message_delta(db, opid, "worker", token, "turn", {"id":"item", "text":"Coral wig looks "})
        await message_delta(db, opid, "worker", token, "turn", {"id":"item", "text":"Coral wig looks soft. "})
        assert (await db.get(Design, design)).last_activity_at == before
    async with factory() as db:
        saved = await db.get(Message, identity)
        assert saved.status == "streaming"
        assert saved.content_parts == [{"type":"text", "text":"Coral wig looks"}]
        events = (await db.execute(select(DomainEvent).where(DomainEvent.event_type == "message.delta")
                                  .order_by(DomainEvent.sequence))).scalars().all()
        assert [row.payload["text"] for row in events] == ["Coral wig", "Coral wig looks"]
        assert all(set(row.payload) == {"message_id","operation_id","branch_id","text"} for row in events)
    async with factory.begin() as db:
        final = {"id":"item", "text":"A soft coral wig."}
        message = await message_completed(db, opid, "worker", token, "turn", final)
        assert message.id == identity and message.history_sequence == sequence
        await message_completed(db, opid, "worker", token, "turn", final)
        await message_delta(db, opid, "worker", token, "turn", {"id":"item","text":"Late duplicate words "})
    async with factory() as db:
        message = await db.get(Message, identity)
        assert message.status == "completed"
        assert message.content_parts == [{"type":"text","text":"A soft coral wig."}]
        rows = (await db.execute(select(DomainEvent).where(DomainEvent.event_type == "message.completed"))).scalars().all()
        assert len(rows) == 1
        assert (await db.get(Wallet, owner)).reserved == 1


@pytest.mark.parametrize("status", [None, "completed", "interrupted", "failed"])
async def test_incomplete_items_are_retained_without_inventing_completion(factory, status):
    owner, design, branch, opid, token = await prepare(factory)
    async with factory.begin() as db:
        message = await message_delta(db, opid, "worker", token, "turn", {"id":"item","text":"Try a coral wig "})
        identity = message.id
    async with factory.begin() as db:
        await finish(db, opid, "worker", token, upstream_status=status)
    async with factory() as db:
        message = await db.get(Message, identity)
        assert message.content_parts == [{"type":"text","text":"Try a coral"}]
        assert message.status == ("streaming" if status is None else "interrupted")
        assert (await db.get(Wallet, owner)).reserved == (1 if status is None else 0)


async def test_stream_is_fenced_and_can_be_hidden_by_authoritative_empty_final(factory):
    owner, design, branch, opid, token = await prepare(factory)
    for worker, candidate_token, turn, error in [
        ("old", token, "turn", "stale_lease"), ("worker", token+1, "turn", "stale_lease"),
        ("worker", token, "other", "turn_identity_conflict")
    ]:
        async with factory.begin() as db:
            with pytest.raises(ApplicationError, match=error):
                await message_delta(db, opid, worker, candidate_token, turn, {"id":"item","text":"Some words here "})
    async with factory.begin() as db:
        message = await message_delta(db, opid, "worker", token, "turn", {"id":"item","text":"Some words here "})
        identity = message.id
        await message_completed(db, opid, "worker", token, "turn",
                                {"id":"item","text":"![image](sandbox:/mnt/data/output.png)"})
    async with factory() as db:
        message = await db.get(Message, identity)
        assert message.status == "completed" and message.content_parts == []
