import logging
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderUsageRecord
from app.db.session import get_sessionmaker
from app.generation.usage import TokenUsage


logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class ProviderUsageSummary:
    provider: str
    calls: int
    successes: int
    failures: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    generated_images: int


async def record_provider_usage(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    provider_preset: str,
    operation: str,
    status: str,
    token_usage: TokenUsage | None = None,
    generated_images: int = 0,
    error_code: str | None = None,
) -> None:
    session.add(
        ProviderUsageRecord(
            provider=provider,
            model=model,
            provider_preset=provider_preset,
            operation=operation,
            status=status,
            input_tokens=token_usage.input_tokens if token_usage else None,
            output_tokens=token_usage.output_tokens if token_usage else None,
            total_tokens=token_usage.total_tokens if token_usage else None,
            generated_images=generated_images,
            error_code=error_code,
        )
    )
    await session.flush()


async def record_provider_usage_safely(**kwargs: object) -> None:
    """Persist provider telemetry without changing an operation's result."""
    try:
        async with get_sessionmaker()() as session:
            await record_provider_usage(session, **kwargs)  # type: ignore[arg-type]
            await session.commit()
    except Exception:
        logger.exception("Unable to record generation provider usage")


async def summarize_provider_usage(session: AsyncSession) -> list[ProviderUsageSummary]:
    result = await session.execute(
        select(
            ProviderUsageRecord.provider,
            func.count(ProviderUsageRecord.id),
            func.sum(case((ProviderUsageRecord.status == "succeeded", 1), else_=0)),
            func.sum(case((ProviderUsageRecord.status == "failed", 1), else_=0)),
            func.sum(ProviderUsageRecord.input_tokens),
            func.sum(ProviderUsageRecord.output_tokens),
            func.sum(ProviderUsageRecord.total_tokens),
            func.coalesce(func.sum(ProviderUsageRecord.generated_images), 0),
        )
        .group_by(ProviderUsageRecord.provider)
        .order_by(ProviderUsageRecord.provider)
    )
    return [
        ProviderUsageSummary(
            provider=str(provider),
            calls=int(calls),
            successes=int(successes or 0),
            failures=int(failures or 0),
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            generated_images=int(generated_images),
        )
        for (
            provider,
            calls,
            successes,
            failures,
            input_tokens,
            output_tokens,
            total_tokens,
            generated_images,
        ) in result.all()
    ]
