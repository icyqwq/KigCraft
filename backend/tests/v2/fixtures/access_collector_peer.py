"""Synthetic collector process, no web server or upstream model."""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.admin.access_collector import AccessCollector

async def main():
    engine=create_async_engine(os.environ["KIG_COLLECTOR_TEST_DATABASE_URL"])
    try:
        collector=AccessCollector(factory=async_sessionmaker(engine))
        if os.environ.get("KIG_COLLECTOR_TEST_FAIL")=="1":
            collector.failed()
        if not await collector.pulse():
            raise RuntimeError("collector fixture failed")
        print(collector.identity)
    finally:
        await engine.dispose()

asyncio.run(main())
