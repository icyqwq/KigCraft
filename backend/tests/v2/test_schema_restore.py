"""Regression for PostgreSQL's own deparse -> parse backup round trip."""
import pytest
from sqlalchemy import text

from app.db.schema_catalog import read_catalog, stable_definition
from app.db.v2_schema import require_v2_schema
from .test_schema_catalog import schema_engine, settings
from .test_migrations import migration_database


async def test_reparse_constraints_and_partial_indexes_preserves_frozen_catalog(schema_engine):
    async with schema_engine.begin() as db:
        before = await read_catalog(db)
        constraints = list((await db.execute(text("""
            SELECT c.relname, x.conname, pg_get_constraintdef(x.oid,false) definition
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname LIKE 'v2_%' AND x.contype='c'
            ORDER BY c.relname,x.conname
        """))).mappings())
        indexes = list((await db.execute(text("""
            SELECT ic.relname, pg_get_indexdef(i.indexrelid,0,false) definition
            FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_class ic ON ic.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname LIKE 'v2_%' AND i.indpred IS NOT NULL
            ORDER BY ic.relname
        """))).mappings())
        quote = db.dialect.identifier_preparer.quote
        for row in constraints:
            table, name = quote(row["relname"]), quote(row["conname"])
            await db.execute(text(f"ALTER TABLE public.{table} DROP CONSTRAINT {name}"))
            await db.execute(text(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} " + row["definition"]))
        for row in indexes:
            await db.execute(text("DROP INDEX public." + quote(row["relname"])))
            await db.execute(text(row["definition"]))
        assert await read_catalog(db) == before
        raw = await db.scalar(text("""
            SELECT pg_get_constraintdef(oid,false) FROM pg_constraint
            WHERE conname='ck_v2_review_cache_verdict'
        """))
        assert "('allow'::character varying)::text" in raw  # Actual PG14 changed representation.
    await require_v2_schema(settings=settings(), engine=schema_engine)
    # A different allowed value is still a structural change after normalization.
    async with schema_engine.begin() as db:
        await db.execute(text("ALTER TABLE v2_asset_review_cache DROP CONSTRAINT ck_v2_review_cache_verdict"))
        await db.execute(text("""ALTER TABLE v2_asset_review_cache ADD CONSTRAINT ck_v2_review_cache_verdict
            CHECK (verdict::text = ANY (ARRAY[('allow'::character varying)::text, ('other'::character varying)::text]))"""))
    with pytest.raises(RuntimeError, match="v2_asset_review_cache:constraints"):
        await require_v2_schema(settings=settings(), engine=schema_engine)


def test_only_equivalent_simple_builtin_array_cast_is_normalized():
    original = "(ARRAY['allow'::character varying, 'reject'::character varying])::text[]"
    restored = "ARRAY[('allow'::character varying)::text, ('reject'::character varying)::text]"
    assert stable_definition(restored) == original
    assert stable_definition("CHECK (verdict = ANY (" + restored + "))") == "CHECK (verdict = ANY (" + original + "))"
    assert stable_definition(original) == original
    for value in [restored.replace("'allow'", "'other'"), restored.replace("'reject'", "'allow'")]:
        assert stable_definition(value) != original
    for value in [
        "ARRAY[('allow'::character varying(2))::text]",
        "ARRAY[(lower('ALLOW')::character varying)::text]",
        "ARRAY[('allow'::custom_type)::text]",
        "ARRAY[('allow'::character varying)::custom_type]",
        "ARRAY[('allow'::character varying)::text, NULL]",
        "ARRAY[('a,b'::character varying)::text]",
        "some" + restored,
    ]:
        assert stable_definition(value) == value


def test_quoted_contents_are_opaque():
    fragment = "ARRAY[('allow'::character varying)::text]"
    literal = "'" + fragment.replace("'", "''") + "'"
    identifier = '"' + fragment + '"'
    for value in [literal, identifier, "CHECK (label = " + literal + ")", "prefix " + identifier]:
        assert stable_definition(value) == value
