from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.base import Base


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


def reset_session_factory() -> None:
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


async def ensure_database_schema() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[table for table in Base.metadata.sorted_tables if not table.info.get("v2")]
            )
        )
        for statement in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_generation_limit_override INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_generation_balance INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS generation_quality_improvement_consent BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS generation_quality_improvement_consent_updated_at TIMESTAMP WITH TIME ZONE",
            "CREATE INDEX IF NOT EXISTS auth_login_failures_email_created_idx ON auth_login_failures(email, created_at)",
            "CREATE INDEX IF NOT EXISTS auth_login_failures_ip_created_idx ON auth_login_failures(client_ip, created_at)",
            "ALTER TABLE album_items ADD COLUMN IF NOT EXISTS image_url VARCHAR(512) NOT NULL DEFAULT ''",
            "ALTER TABLE album_items ADD COLUMN IF NOT EXISTS recipe JSON",
            "ALTER TABLE album_items ADD COLUMN IF NOT EXISTS metadata JSON",
            "ALTER TABLE album_items ALTER COLUMN project_id DROP NOT NULL",
            "ALTER TABLE album_items ALTER COLUMN output_id DROP NOT NULL",
            "CREATE INDEX IF NOT EXISTS album_items_user_created_idx ON album_items(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS album_items_image_url_idx ON album_items(image_url)",
        ]:
            await connection.execute(text(statement))


async def drop_database_schema() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
