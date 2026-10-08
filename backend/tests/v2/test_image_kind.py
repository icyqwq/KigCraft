"""Image kind: a version is a finished head-shell photo or a character reference, fixed when it is inserted."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.conversation.replies import REPLY_INSTRUCTIONS, REPLY_SCHEMA, message_image_kind, message_kind, message_parts
from app.design.models import Revision
from app.design.router import message_json
from .legacy_migration_seed import setup as legacy_setup
from .test_migrations import ROOT, migrate, migration_database
from .test_operations import factory
from .test_revision_numbers import migrate_to
from .test_worker import ScriptedRpc, png, prepared


def envelope(version=2, kind="response", text="Done.", **extra):
    return json.dumps({"schema_version": version, "kind": kind, "text": text, **extra})


# ── reply parsing: no database needed ────────────────────────────────────────────────────

def test_reply_schema_is_version_two_and_declares_image_kind_as_required_nullable_enum():
    assert REPLY_SCHEMA["properties"]["schema_version"]["enum"] == [2]
    assert "image_kind" in REPLY_SCHEMA["required"] and REPLY_SCHEMA["additionalProperties"] is False
    declared = REPLY_SCHEMA["properties"]["image_kind"]
    assert declared["type"] == ["string", "null"] and declared["enum"] == ["head_shell", "reference", None]
    assert "image_kind" in REPLY_INSTRUCTIONS and "schema_version 2" in REPLY_INSTRUCTIONS


def test_v1_replies_still_parse_and_keep_the_old_marker_shape():
    parts = message_parts({"text": envelope(1), "phase": "final_answer"})
    assert parts == [{"type": "text", "text": "Done."}, {"type": "reply_intent", "schema_version": 1, "kind": "response"}]
    assert message_kind(parts) == "response" and message_image_kind(parts) is None


@pytest.mark.parametrize("declared", ["head_shell", "reference"])
def test_v2_reply_records_the_declared_kind_in_the_internal_marker(declared):
    parts = message_parts({"text": envelope(2, image_kind=declared), "phase": "final_answer"})
    assert parts[1] == {"type": "reply_intent", "schema_version": 1, "kind": "response", "image_kind": declared}
    assert message_kind(parts) == "response" and message_image_kind(parts) == declared


@pytest.mark.parametrize("body", [envelope(2), envelope(2, image_kind=None)])
def test_v2_reply_without_a_declaration_has_no_kind(body):
    parts = message_parts({"text": body, "phase": "final_answer"})
    assert parts[1] == {"type": "reply_intent", "schema_version": 1, "kind": "response"}
    assert message_image_kind(parts) is None


@pytest.mark.parametrize("body", [
    envelope(1, image_kind="reference"),       # the v1 envelope has no such field
    envelope(2, image_kind="photo"),           # unknown kind
    envelope(3, image_kind="reference"),       # unknown reply version
    envelope(2, image_kind=True),
])
def test_invalid_declarations_are_rejected_like_any_malformed_envelope(body):
    assert message_parts({"text": body, "phase": "final_answer"}) == [
        {"type": "reply_intent", "schema_version": 1, "kind": "invalid"}]


def test_image_kind_is_internal_and_never_reaches_the_public_message():
    parts = message_parts({"text": envelope(2, image_kind="reference"), "phase": "final_answer"})
    public = message_json(SimpleNamespace(id="m", history_sequence=1, branch_id="b", role="assistant", content_parts=parts,
        operation_id="o", status="completed", created_at="2026-01-01T00:00:00Z"))
    assert "image_kind" not in json.dumps(public, default=str) and "reply_intent" not in json.dumps(public, default=str)


@pytest.mark.parametrize("parts", [
    [{"type": "text", "text": "x"}],
    [{"type": "reply_intent", "schema_version": 2, "kind": "response", "image_kind": "reference"}],
    [{"type": "reply_intent", "schema_version": 1, "kind": "response", "image_kind": "photo"}],
    [{"type": "reply_intent", "schema_version": 1, "kind": "response", "image_kind": "reference"},
     {"type": "reply_intent", "schema_version": 1, "kind": "response", "image_kind": "reference"}],
])
def test_only_one_well_formed_marker_can_declare_a_kind(parts):
    assert message_image_kind(parts) is None


# ── publication: needs PostgreSQL ────────────────────────────────────────────────────────

async def publish_with_final_message(factory, tmp_path, monkeypatch, final_text):
    original = ScriptedRpc.request

    async def request(self, method, params):
        response = await original(self, method, params)
        if self.images and method == "turn/start":
            self.events = [self.item({"id": "image", "type": "imageGeneration", "status": "completed", "result": png()}),
                           self.item({"id": "final", "type": "agentMessage", "phase": "final_answer", "text": final_text}),
                           self.complete("completed")]
        return response
    monkeypatch.setattr(ScriptedRpc, "request", request)
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    await worker.run_once()
    async with factory() as db:
        return (await db.scalars(select(Revision))).all()


@pytest.mark.parametrize("final,expected", [
    (envelope(2, image_kind="reference"), "reference"),
    (envelope(2, image_kind="head_shell"), "head_shell"),
    (envelope(2, image_kind=None), "head_shell"),
    (envelope(1), "head_shell"),
    ("Your design is ready.", "head_shell"),
])
async def test_version_is_written_with_the_kind_the_final_reply_declared(factory, tmp_path, monkeypatch, final, expected):
    revisions = await publish_with_final_message(factory, tmp_path, monkeypatch, final)
    assert [item.image_kind for item in revisions] == [expected]


async def test_published_version_kind_cannot_be_rewritten(factory, tmp_path, monkeypatch):
    revisions = await publish_with_final_message(factory, tmp_path, monkeypatch, envelope(2, image_kind="reference"))
    async with factory() as db:
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(text("UPDATE v2_revisions SET image_kind='head_shell' WHERE id=:id"), {"id": revisions[0].id})


# ── migration: needs PostgreSQL ──────────────────────────────────────────────────────────

async def test_migration_defaults_existing_versions_to_head_shell_and_validates_the_enum(migration_database):
    assert migrate_to(migration_database, "v2_0027").returncode == 0
    engine = create_async_engine(migration_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        owner, design, branch = await legacy_setup(factory)
        asset, revision = str(uuid4()), str(uuid4())
        async with factory.begin() as db:
            await db.execute(text("""INSERT INTO v2_assets (id,owner_id,object_key,sha256,mime_type,kind,width,height,byte_size,status,created_at)
                VALUES (:id,:owner,:key,:sha,'image/png','master',16,16,100,'ready',now())"""),
                {"id": asset, "owner": owner, "key": str(uuid4()), "sha": "a" * 64})
            await db.execute(text("""INSERT INTO v2_revisions (id,design_id,branch_id,clean_asset_id,root_source_asset_id,source_kind,view,created_at)
                VALUES (:id,:design,:branch,:asset,:asset,'ai','front',now())"""),
                {"id": revision, "design": design, "branch": branch, "asset": asset})
        assert migrate(migration_database).returncode == 0
        async with factory() as db:
            assert await db.scalar(text("SELECT image_kind FROM v2_revisions WHERE id=:id"), {"id": revision}) == "head_shell"
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "v2_0029"
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text("UPDATE v2_revisions SET image_kind='reference' WHERE id=:id"), {"id": revision})
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text("""INSERT INTO v2_revisions (id,design_id,branch_id,clean_asset_id,root_source_asset_id,source_kind,view,image_kind,created_at)
                    VALUES (:id,:design,:branch,:asset,:asset,'ai','front','photo',now())"""),
                    {"id": str(uuid4()), "design": design, "branch": branch, "asset": asset})
    finally:
        await engine.dispose()
