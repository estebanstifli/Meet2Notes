from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from local_meeting_ai.domain.entities import Job
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import JobCancelledError
from local_meeting_ai.infrastructure.database.repositories import JobRepository

logger = logging.getLogger(__name__)
JobHandler = Callable[[Job, "JobContext"], Awaitable[dict[str, Any]]]


class JobContext:
    def __init__(self, job_uuid: str, repository: JobRepository) -> None:
        self.job_uuid = job_uuid
        self.repository = repository

    async def update(self, progress: float, message: str) -> None:
        self.repository.update_progress(self.job_uuid, progress, message)
        await asyncio.sleep(0)

    async def raise_if_cancelled(self) -> None:
        job = self.repository.get(self.job_uuid)
        if not job or job.cancel_requested:
            raise JobCancelledError("The job was cancelled")
        await asyncio.sleep(0)


class LocalJobQueue:
    """Persistent local queue with cooperative cancellation."""

    def __init__(self, repository: JobRepository, worker_count: int = 1) -> None:
        self.repository = repository
        self.worker_count = worker_count
        self.handlers: dict[JobType, JobHandler] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._started = False

    def register(self, job_type: JobType, handler: JobHandler) -> None:
        self.handlers[job_type] = handler

    async def start(self) -> None:
        if self._started:
            return
        interrupted = self.repository.recover_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted job(s) as failed", interrupted)
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"local-job-worker-{index}")
            for index in range(self.worker_count)
        ]
        for job in self.repository.queued():
            await self._queue.put(job.uuid)

    async def stop(self) -> None:
        self._started = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, job_uuid: str) -> None:
        if not self._started:
            raise RuntimeError("The job queue has not started")
        await self._queue.put(job_uuid)

    async def _worker(self, index: int) -> None:
        logger.info("Job worker %d started", index)
        while True:
            job_uuid = await self._queue.get()
            try:
                await self._run_job(job_uuid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected queue failure for job %s", job_uuid)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_uuid: str) -> None:
        queued_job = self.repository.get(job_uuid)
        if not queued_job:
            return
        if queued_job.cancel_requested:
            self.repository.mark_cancelled(job_uuid)
            return
        job = self.repository.start(job_uuid)
        if not job:
            return

        handler = self.handlers.get(job.job_type)
        if not handler:
            self.repository.fail(job_uuid, f"No handler is registered for {job.job_type.value}")
            return

        context = JobContext(job_uuid, self.repository)
        try:
            result = await handler(job, context)
            await context.raise_if_cancelled()
            self.repository.complete(job_uuid, result)
        except JobCancelledError:
            self.repository.mark_cancelled(job_uuid)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Job %s failed", job_uuid)
            self.repository.fail(job_uuid, str(error) or type(error).__name__)
