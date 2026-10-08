import os
import subprocess
import sys
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.design.models import LegacyMaterial
from .test_migrations import migration_database, migrate, ROOT
from .test_revision_numbers import migrate_to
from .test_legacy_materials import prepared, scan
from .test_legacy_material_import import apply
from .legacy_migration_seed import fingerprints


async def test_0021_preserves_old_tables_and_refuses_material_loss(migration_database, tmp_path):
    assert migrate_to(migration_database, "v2_0020").returncode == 0
    engine = create_async_engine(migration_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        ids, root, _ = await prepared(factory, tmp_path)
        before = await fingerprints(factory)
        assert migrate(migration_database).returncode == 0
        assert migrate(migration_database).returncode == 0
        after = await fingerprints(factory)
        assert set(after) - set(before) == {"v2_legacy_materials", "v2_context_observations", "v2_account_closures", "v2_account_content_retirements"}
        assert all(after[name] == value for name, value in before.items() if name != "alembic_version")
        await apply(factory, tmp_path, ids, root, await scan(factory, tmp_path, ids, root))
        result = subprocess.run([sys.executable, "-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0020')"],
            cwd=ROOT, env=os.environ | {"V2_DATABASE_URL": migration_database, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode != 0 and "Refuse to discard legacy material provenance" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "v2_0029"
            assert len(list((await db.scalars(select(LegacyMaterial))).all())) == 3
    finally:
        await engine.dispose()
