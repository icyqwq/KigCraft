import asyncio
import os
from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
url = os.environ.get("V2_DATABASE_URL")
if not url or not url.startswith("postgresql+asyncpg://"):
    raise RuntimeError("Set V2_DATABASE_URL explicitly to the PostgreSQL migration target")

def migrate(connection):
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()

async def online():
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(7272192201)"))
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
