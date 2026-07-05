import asyncio
import logging
import time

from app.core.config import get_settings
from app.generation.job_store import StoredOutput, job_store
from app.generation.provider import ImageGenerationProvider, ProviderOutput, ProviderUsage

logger = logging.getLogger("uvicorn.error")


def to_stored_output(output: ProviderOutput) -> StoredOutput:
    return StoredOutput(
        index=output.index,
        object_key=output.object_key,
        image_url=output.image_url,
        width=output.width,
        height=output.height,
        landmarks=output.landmarks,
    )


class GenerationQueue:
    def __init__(self, parallelism: int = 8) -> None:
        self._semaphore = asyncio.Semaphore(parallelism)
        self._pending_job_ids: list[str] = []

    def submit_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        self._queue_job(job_id)
        asyncio.create_task(self._run_queued_job(job_id, provider))

    def clear_pending(self) -> None:
        self._pending_job_ids.clear()

    async def run_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        self._queue_job(job_id)
        await self._run_queued_job(job_id, provider)

    async def _run_queued_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        async with self._semaphore:
            self._start_job(job_id)
            started_at = time.monotonic()
            logger.info("Generation job started job_id=%s provider=%s", job_id, provider.name)
            try:
                await self._transition(job_id, "preparing_references", 15, "准备素材")
                await self._transition(job_id, "generating", 45, "生成中")
                job = job_store.get(job_id)
                prompt_payload = job.prompt_payload if job else {}
                expected_count = job.expected_output_count if job else 4
                logger.info(
                    "Generation provider invoked job_id=%s provider=%s mode=%s expected_outputs=%d reference_count=%d",
                    job_id,
                    provider.name,
                    prompt_payload.get("generation_mode") or "front_design",
                    expected_count,
                    len(prompt_payload.get("reference_keys") or []),
                )
                async for provider_item in provider.generate_incremental(
                    job_id, prompt_payload
                ):
                    if isinstance(provider_item, ProviderUsage):
                        stored_job = job_store.record_token_usage(
                            job_id, provider_item.token_usage
                        )
                        logger.info(
                            "Generation token usage recorded job_id=%s provider=%s usage=%s",
                            job_id,
                            provider.name,
                            provider_item.token_usage.to_dict(),
                        )
                        if stored_job and job_store.should_stop(job_id):
                            logger.info("Generation job stop requested after token usage job_id=%s", job_id)
                            break
                        continue

                    provider_output = provider_item
                    if job_store.should_stop(job_id):
                        logger.info("Generation job stop requested before storing output job_id=%s", job_id)
                        break
                    job_store.append_output(job_id, to_stored_output(provider_output))
                    stored_job = job_store.get(job_id)
                    completed_count = len(stored_job.outputs) if stored_job else 0
                    logger.info(
                        "Generation output stored job_id=%s provider=%s mode=%s index=%d completed=%d/%d image_url=%s",
                        job_id,
                        provider.name,
                        prompt_payload.get("generation_mode") or "front_design",
                        provider_output.index,
                        completed_count,
                        expected_count,
                        provider_output.image_url,
                    )
                    progress = min(85, 45 + int((completed_count / max(1, expected_count)) * 40))
                    job_store.update(
                        job_id,
                        status="generating",
                        progress=progress,
                        phase_label=f"结果 {completed_count}/{expected_count} 已完成",
                        event_type="candidate_ready",
                        message=f"结果 {provider_output.index} 已完成",
                    )
                    if job_store.should_stop(job_id):
                        logger.info("Generation job stop requested after output job_id=%s", job_id)
                        break

                job = job_store.get(job_id)
                if job and job.accepted_output_index is not None:
                    logger.info("Generation job accepted early job_id=%s output_index=%s", job_id, job.accepted_output_index)
                    return
                if not job or len(job.outputs) != expected_count:
                    actual_count = len(job.outputs) if job else 0
                    raise RuntimeError(
                        f"Generation provider returned {actual_count} outputs; expected {expected_count}"
                    )

                await self._transition(job_id, "saving_outputs", 90, "保存结果")
                job_store.update(
                    job_id,
                    status="succeeded",
                    progress=100,
                    phase_label="生成完成",
                    event_type="succeeded",
                    message="生成完成",
                )
                logger.info(
                    "Generation job succeeded job_id=%s provider=%s mode=%s duration=%.1fs outputs=%d",
                    job_id,
                    provider.name,
                    prompt_payload.get("generation_mode") or "front_design",
                    time.monotonic() - started_at,
                    len(job.outputs),
                )
            except Exception as exc:
                job = job_store.get(job_id)
                if job and job.accepted_output_index is not None:
                    logger.info("Generation job failed after accepted output; ignoring job_id=%s", job_id)
                    return
                logger.exception("Generation job failed job_id=%s provider=%s", job_id, provider.name)
                job_store.update(
                    job_id,
                    status="failed",
                    progress=job.progress if job else 0,
                    phase_label="生成失败",
                    event_type="failed",
                    message=str(exc) or "生成失败",
                )
            finally:
                self._refresh_queue_positions()

    def _queue_job(self, job_id: str) -> None:
        if job_id not in self._pending_job_ids:
            self._pending_job_ids.append(job_id)
        self._refresh_queue_positions()

    def _start_job(self, job_id: str) -> None:
        if job_id in self._pending_job_ids:
            self._pending_job_ids.remove(job_id)
        self._refresh_queue_positions()

    def _refresh_queue_positions(self) -> None:
        for index, pending_job_id in enumerate(self._pending_job_ids, start=1):
            job_store.set_queue_position(pending_job_id, index)

    async def _transition(
        self,
        job_id: str,
        status: str,
        progress: int,
        phase_label: str,
    ) -> None:
        await asyncio.sleep(0.01)
        job_store.update(
            job_id,
            status=status,
            progress=progress,
            phase_label=phase_label,
            event_type=status,
            message=phase_label,
        )


generation_queue = GenerationQueue(parallelism=get_settings().generation_parallelism)
