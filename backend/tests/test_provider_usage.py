import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models import ProviderUsageRecord
from app.db.session import get_sessionmaker
from app.generation.provider_usage import record_provider_usage, summarize_provider_usage
from app.generation.usage import TokenUsage


@pytest_asyncio.fixture
async def session(test_app):
    async with get_sessionmaker()() as db_session:
        await db_session.execute(delete(ProviderUsageRecord))
        await db_session.commit()
        yield db_session
        await db_session.rollback()
        await db_session.execute(delete(ProviderUsageRecord))
        await db_session.commit()


async def test_provider_usage_record_persists_safe_operation_metadata(session):
    await record_provider_usage(
        session,
        provider="qwen",
        model="qwen3.7-plus",
        provider_preset="qwen_seedream",
        operation="detail_analysis",
        status="failed",
        token_usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
        error_code="provider_timeout",
    )
    await session.commit()

    record = (await session.execute(select(ProviderUsageRecord))).scalar_one()
    assert record.provider == "qwen"
    assert record.model == "qwen3.7-plus"
    assert record.provider_preset == "qwen_seedream"
    assert record.operation == "detail_analysis"
    assert record.status == "failed"
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (12, 8, 20)
    assert record.generated_images == 0
    assert record.error_code == "provider_timeout"
    assert record.created_at is not None


async def test_provider_summary_has_separate_qwen_and_seedream_rows(session):
    await record_provider_usage(
        session,
        provider="qwen",
        model="qwen3.7-plus",
        provider_preset="qwen_seedream",
        operation="detail_analysis",
        status="succeeded",
        token_usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
    )
    await record_provider_usage(
        session,
        provider="seedream",
        model="Seedream 5.0 Pro",
        provider_preset="qwen_seedream",
        operation="generation",
        status="succeeded",
        generated_images=1,
    )
    await session.commit()

    rows = await summarize_provider_usage(session)

    assert {(row.provider, row.calls, row.generated_images) for row in rows} == {
        ("qwen", 1, 0),
        ("seedream", 1, 1),
    }


async def test_provider_summary_aggregates_status_tokens_and_images(session):
    await record_provider_usage(
        session,
        provider="qwen",
        model="qwen3.7-plus",
        provider_preset="qwen_seedream",
        operation="reference_safety",
        status="succeeded",
        token_usage=TokenUsage(input_tokens=10, output_tokens=3, total_tokens=13),
    )
    await record_provider_usage(
        session,
        provider="qwen",
        model="qwen3.7-plus",
        provider_preset="qwen_seedream",
        operation="detail_analysis",
        status="failed",
        token_usage=TokenUsage(input_tokens=7, output_tokens=2, total_tokens=9),
        error_code="invalid_response",
    )
    await session.commit()

    summary = (await summarize_provider_usage(session))[0]

    assert summary.provider == "qwen"
    assert summary.calls == 2
    assert summary.successes == 1
    assert summary.failures == 1
    assert summary.input_tokens == 17
    assert summary.output_tokens == 5
    assert summary.total_tokens == 22
    assert summary.generated_images == 0
