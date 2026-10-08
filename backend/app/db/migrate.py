"""Explicit controlled migrations. Requires V2_DATABASE_URL; never reads .env.

python -m app.db.migrate baseline   # validate an existing legacy schema, then stamp
python -m app.db.migrate upgrade    # expand schema in a separate deployment job
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]


def migration_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def validate_legacy(connection) -> None:
    expected = json.loads((ROOT / "alembic/legacy_schema.json").read_text(encoding="utf-8"))
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if any(name.startswith("v2_") for name in tables):
        raise RuntimeError("V2 tables already exist; inspect migration state instead of stamping")
    if "alembic_version" in tables:
        raise RuntimeError("Migration version already exists; do not overwrite it")
    problems = []
    dialect = postgresql.dialect()
    for name, schema in expected.items():
        if name not in tables:
            problems.append(f"{name}: missing table")
            continue
        actual = {c["name"]: {"type": str(c["type"].compile(dialect=dialect)), "nullable": c["nullable"]}
                  for c in inspector.get_columns(name)}
        if actual != schema["columns"]:
            problems.append(f"{name}: column drift")
        if inspector.get_pk_constraint(name)["constrained_columns"] != schema["primary_key"]:
            problems.append(f"{name}: primary key drift")
        indexes = {i["name"]: {"columns": i["column_names"], "unique": bool(i["unique"])}
                   for i in inspector.get_indexes(name)}
        if any(indexes.get(key) != value for key, value in schema["indexes"].items()):
            problems.append(f"{name}: index drift")
    if problems:
        raise RuntimeError("Legacy baseline refused: " + "; ".join(problems))


async def baseline(url: str) -> None:
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            # Serialize adoption, hold a table lock so validation/stamp cannot race writes/DDL.
            await connection.execute(text("SELECT pg_advisory_xact_lock(7272192201)"))
            await connection.execute(text("LOCK TABLE users IN ACCESS SHARE MODE"))
            await connection.run_sync(validate_legacy)
            def stamp(sync_connection):
                from alembic.migration import MigrationContext
                from alembic.script import ScriptDirectory
                context = MigrationContext.configure(sync_connection)
                context.stamp(ScriptDirectory.from_config(migration_config()), "v2_0001")
            await connection.run_sync(stamp)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["baseline", "upgrade"])
    args = parser.parse_args()
    url = os.environ.get("V2_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        parser.error("Set V2_DATABASE_URL explicitly")
    if args.action == "baseline":
        asyncio.run(baseline(url))
    else:
        command.upgrade(migration_config(), "head")


if __name__ == "__main__":
    main()
