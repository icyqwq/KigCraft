import asyncio
import logging
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.generation.job_store import StoredOutput, job_store
from app.generation.message_codes import (
    GENERATION_COMPLETE,
    GENERATION_FAILED,
    GENERATING,
    PREPARING_REFERENCES,
    SAVING_OUTPUTS,
    candidate_progress,
    candidate_ready,
)
from app.generation.provider import ImageGenerationProvider, ProviderOutput, ProviderUsage
from app.generation.retired import reject_retired
from app.generation.reliability import GenerationMetrics, current_metrics, classify_failure

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class QueueStatus:
    pending_count: int
    active_count: int
    parallelism: int


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
        self._parallelism = max(1, parallelism)
        self._condition = asyncio.Condition()
        self._pending_job_ids: list[str] = []
        self._active_job_ids: set[str] = set()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        reject_retired(provider.name)
        self._queue_job(job_id)
        task = asyncio.create_task(self._run_queued_job(job_id, provider))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job_id, None))

    def clear_pending(self) -> None:
        self._pending_job_ids.clear()
        self._schedule_refresh_queue_positions()
        self._notify_waiters()

    async def cancel_all_and_wait(self) -> None:
        job_ids = set(self._pending_job_ids) | set(self._active_job_ids) | set(self._tasks)
        for job_id in job_ids:
            await job_store.cancel(job_id)
        self.clear_pending()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_job(self, job_id: str) -> None:
        if job_id in self._pending_job_ids:
            self._pending_job_ids.remove(job_id)
        self._schedule_refresh_queue_positions()
        self._notify_waiters()

    def status(self) -> QueueStatus:
        return QueueStatus(
            pending_count=len(self._pending_job_ids),
            active_count=len(self._active_job_ids),
            parallelism=self._parallelism,
        )

    async def run_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        self._queue_job(job_id)
        await self._run_queued_job(job_id, provider)

    async def _run_queued_job(self, job_id: str, provider: ImageGenerationProvider) -> None:
        if not await self._wait_for_turn(job_id):
            return
        metrics = GenerationMetrics(parallelism=self._parallelism, active_jobs_at_start=len(self._active_job_ids))
        metrics_token = current_metrics.set(metrics)
        try:
            started_at = time.monotonic()
            logger.info("Generation job started job_id=%s provider=%s", job_id, provider.name)
            try:
                async with asyncio.timeout(float(getattr(get_settings(), "generation_job_timeout_seconds", 1500))):
                    await self._transition(job_id, "preparing_references", 15, PREPARING_REFERENCES)
                    await self._transition(job_id, "generating", 45, GENERATING)
                    job = await job_store.get(job_id)
                    prompt_payload = job.prompt_payload if job else {}
                    expected_count = job.expected_output_count if job else 4
                    logger.info(
                        "Generation provider invoked job_id=%s provider=%s provider_preset=%s mode=%s expected_outputs=%d reference_count=%d",
                        job_id,
                        provider.name,
                        prompt_payload.get("provider_preset") or (job.provider if job else "unknown"),
                        prompt_payload.get("generation_mode") or "front_design",
                        expected_count,
                        len(prompt_payload.get("reference_keys") or []),
                    )
                    async for provider_item in provider.generate_incremental(
                        job_id, prompt_payload
                    ):
                        if isinstance(provider_item, ProviderUsage):
                            stored_job = await job_store.record_token_usage(
                                job_id, provider_item.token_usage
                            )
                            logger.info(
                                "Generation token usage recorded job_id=%s provider=%s usage=%s",
                                job_id,
                                provider.name,
                                provider_item.token_usage.to_dict(),
                            )
                            if stored_job and await job_store.should_stop(job_id):
                                logger.info("Generation job stop requested after token usage job_id=%s", job_id)
                                break
                            continue

                        metrics.image_generated = True
                        metrics.stage = "saving_outputs"
                        provider_output = provider_item
                        if await job_store.should_stop(job_id):
                            logger.info("Generation job stop requested before storing output job_id=%s", job_id)
                            break
                        await job_store.append_output(job_id, to_stored_output(provider_output))
                        stored_job = await job_store.get(job_id)
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
                        await job_store.update(
                            job_id,
                            status="generating",
                            progress=progress,
                            phase_label=candidate_progress(completed_count, expected_count),
                            event_type="candidate_ready",
                            message=candidate_ready(provider_output.index),
                            payload=metrics.payload(),
                        )
                        if await job_store.should_stop(job_id):
                            logger.info("Generation job stop requested after output job_id=%s", job_id)
                            break

                    job = await job_store.get(job_id)
                    if job and job.accepted_output_index is not None:
                        logger.info("Generation job accepted early job_id=%s output_index=%s", job_id, job.accepted_output_index)
                        return
                    if job and job.stop_requested:
                        logger.info("Generation job cancelled job_id=%s", job_id)
                        return
                    if not job or len(job.outputs) != expected_count:
                        actual_count = len(job.outputs) if job else 0
                        raise RuntimeError(
                            f"Generation provider returned {actual_count} outputs; expected {expected_count}"
                        )

                    metrics.stage = "saving_outputs"
                    await self._transition(job_id, "saving_outputs", 90, SAVING_OUTPUTS)
                    metrics.duration_seconds = round(time.monotonic() - started_at, 3)
                    await job_store.update(
                        job_id,
                        status="succeeded",
                        progress=100,
                        phase_label=GENERATION_COMPLETE,
                        event_type="succeeded",
                        message=GENERATION_COMPLETE,
                        payload=metrics.payload(),
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
                metrics.duration_seconds = round(time.monotonic() - started_at, 3)
                metrics.failure_code = classify_failure(exc)
                job = await job_store.get(job_id)
                if job and job.accepted_output_index is not None:
                    logger.info("Generation job failed after accepted output; ignoring job_id=%s", job_id)
                    return
                if job and job.stop_requested:
                    logger.info("Generation job stopped after cancellation job_id=%s", job_id)
                    return
                logger.exception("Generation job failed job_id=%s provider=%s", job_id, provider.name)
                await job_store.update(
                    job_id,
                    status="failed",
                    progress=job.progress if job else 0,
                    phase_label=GENERATION_FAILED,
                    event_type="failed",
                    message=str(exc) or GENERATION_FAILED,
                    payload=metrics.payload(),
                )
            finally:
                logger.info("Generation reliability job_id=%s metrics=%s", job_id, metrics.payload())
                self._finish_job(job_id)
                await self._refresh_queue_positions()
        finally:
            current_metrics.reset(metrics_token)

    async def _wait_for_turn(self, job_id: str) -> bool:
        async with self._condition:
            while True:
                if job_id not in self._pending_job_ids:
                    return False
                if (
                    self._parallelism > len(self._active_job_ids)
                    and self._pending_job_ids.index(job_id)
                    < self._parallelism - len(self._active_job_ids)
                ):
                    break
                if await job_store.should_stop(job_id):
                    return False
                await self._condition.wait()
            self._pending_job_ids.remove(job_id)
            self._active_job_ids.add(job_id)
            await self._refresh_queue_positions()
            self._condition.notify_all()
            return True

    def _queue_job(self, job_id: str, *, schedule_refresh: bool = True) -> None:
        if job_id not in self._pending_job_ids:
            self._pending_job_ids.append(job_id)
        if schedule_refresh:
            self._schedule_refresh_queue_positions()
        self._notify_waiters()

    def _finish_job(self, job_id: str) -> None:
        self._active_job_ids.discard(job_id)
        self._notify_waiters()

    def _notify_waiters(self) -> None:
        async def notify() -> None:
            async with self._condition:
                self._condition.notify_all()

        try:
            asyncio.get_running_loop().create_task(notify())
        except RuntimeError:
            pass

    def _schedule_refresh_queue_positions(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self._refresh_queue_positions())
        except RuntimeError:
            pass

    async def _refresh_queue_positions(self) -> None:
        for index, pending_job_id in enumerate(self._pending_job_ids, start=1):
            await job_store.set_queue_position(pending_job_id, index)

    async def _transition(
        self,
        job_id: str,
        status: str,
        progress: int,
        phase_label: str,
    ) -> None:
        await asyncio.sleep(0.01)
        if await job_store.should_stop(job_id):
            return
        await job_store.update(
            job_id,
            status=status,
            progress=progress,
            phase_label=phase_label,
            event_type=status,
            message=phase_label,
        )


generation_queue = GenerationQueue(parallelism=get_settings().generation_parallelism)
