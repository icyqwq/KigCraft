from sqlalchemy import text
from app.core.config import get_settings
from app.db.session import get_engine
from app.db.schema_catalog import validate_catalog

V2_SCHEMA_VERSION = "v2_0029"


async def require_v2_schema(*, settings=None, engine=None):
    settings = settings if settings is not None else get_settings()
    if settings.v2_runtime_mode == "local" and settings.app_env not in {"development", "test"}:
        raise RuntimeError("Local Codex runtime is restricted to development/test")
    engine = engine if engine is not None else get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
        # Share with other readers, exclude the explicit migration job during inspection.
        await connection.execute(text("SELECT pg_advisory_xact_lock_shared(7272192201)"))
        version = (await connection.execute(text("SELECT version_num FROM public.alembic_version"))).scalar_one_or_none()
        if version != V2_SCHEMA_VERSION:
            raise RuntimeError("Run the explicit V2 migration job before enabling V2")
        return await validate_catalog(connection, V2_SCHEMA_VERSION)
