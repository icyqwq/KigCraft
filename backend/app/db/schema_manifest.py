"""Export a review candidate from an EMPTY, explicitly migrated local QA database.

KIG_V2_SCHEMA_DATABASE_URL must name a loopback kig_v2_schema_* or kig_v2_test_* database.
This command never creates, migrates, repairs or deletes a database, and never
replaces the application's checked-in manifest. Review the candidate and run
the PostgreSQL schema tests before adopting it.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema_catalog import read_catalog, signature
from app.db.v2_schema import V2_SCHEMA_VERSION


async def export_candidate(url: str, destination: Path) -> dict:
    target = make_url(url)
    if (target.drivername != "postgresql+asyncpg" or target.host not in {"127.0.0.1", "localhost"}
            or not (target.database or "").startswith(("kig_v2_schema_", "kig_v2_test_"))):
        raise ValueError("isolated_schema_database_required")
    if destination.exists():
        raise ValueError("new_output_path_required")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            await connection.execute(text("SELECT pg_advisory_xact_lock_shared(7272192201)"))
            version = (await connection.execute(text("SELECT version_num FROM public.alembic_version"))).scalar_one()
            if version != V2_SCHEMA_VERSION:
                raise ValueError("explicit_migration_required")
            catalog = await read_catalog(connection)
            for name in catalog["tables"]:
                if name == "alembic_version":
                    continue
                # Quote catalog identifiers; never interpolate raw SQL from a manifest.
                table = '"public"."' + name.replace('"', '""') + '"'
                if (await connection.execute(text("SELECT EXISTS (SELECT 1 FROM " + table + " LIMIT 1)"))).scalar_one():
                    raise ValueError("empty_schema_database_required")
            major = int((await connection.execute(text("SHOW server_version_num"))).scalar_one()) // 10000
        manifest = {"format": 1, "schema_version": version, "verified_postgresql_majors": [major],
                    "catalog": catalog, "sha256": signature(catalog)}
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
        return {"status": "candidate_exported_requires_review", "sha256": manifest["sha256"],
                "tables": len(catalog["tables"]), "postgresql_major": major}
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(export_candidate(os.environ.get("KIG_V2_SCHEMA_DATABASE_URL", ""), args.output))
    except Exception:
        # Database errors can contain private connection details or SQL defaults.
        print(json.dumps({"status": "refused", "code": "schema_candidate_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
