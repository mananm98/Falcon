from __future__ import annotations

import asyncio
import logging
import traceback

from app.config import settings
from app.database import get_db
from app.pipeline.orchestrator import WikiGenerationPipeline

logger = logging.getLogger(__name__)


class JobOrchestrator:
    """SQLite-backed async job queue for wiki generation."""

    def __init__(self):
        self.max_concurrent = settings.max_concurrent_jobs
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self.active_jobs: dict[str, asyncio.Task] = {}
        self._running = False
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the job queue. Called on FastAPI startup."""
        # Crash recovery: reset any running jobs back to queued
        db = await get_db()
        try:
            await db.execute(
                "UPDATE jobs SET status = 'queued', worker_id = NULL WHERE status = 'running'"
            )
            await db.commit()
        finally:
            await db.close()

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"Job orchestrator started (max concurrent: {self.max_concurrent})")

    async def stop(self) -> None:
        """Stop the job queue. Called on FastAPI shutdown."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        # Cancel active jobs
        for job_id, task in self.active_jobs.items():
            task.cancel()
        logger.info("Job orchestrator stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.semaphore.acquire()
                job = await self._dequeue_next()
                if job:
                    task = asyncio.create_task(self._run_job(job))
                    self.active_jobs[job["id"]] = task
                else:
                    self.semaphore.release()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in job poll loop")
                self.semaphore.release()

            await asyncio.sleep(settings.job_poll_interval_seconds)

    async def _dequeue_next(self) -> dict | None:
        """Atomically claim the next queued job."""
        db = await get_db()
        try:
            async with db.execute(
                """UPDATE jobs
                   SET status = 'running', started_at = datetime('now'), attempts = attempts + 1
                   WHERE id = (
                       SELECT id FROM jobs
                       WHERE status = 'queued' AND attempts < max_attempts
                       ORDER BY priority DESC, created_at ASC
                       LIMIT 1
                   )
                   RETURNING *""",
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    await db.commit()
                    return dict(row)
                return None
        finally:
            await db.close()

    async def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        wiki_id = job["wiki_id"]
        logger.info(f"Starting job {job_id} for wiki {wiki_id}")

        try:
            pipeline = WikiGenerationPipeline(wiki_id)
            await pipeline.execute()
            await self._complete_job(job_id)
            logger.info(f"Job {job_id} completed successfully")
        except Exception as e:
            logger.exception(f"Job {job_id} failed")
            await self._fail_job(job_id, str(e), job.get("attempts", 0), job.get("max_attempts", 3))
        finally:
            self.semaphore.release()
            self.active_jobs.pop(job_id, None)

    async def _complete_job(self, job_id: str) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE jobs SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
                (job_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def _fail_job(
        self, job_id: str, error: str, attempts: int, max_attempts: int
    ) -> None:
        db = await get_db()
        try:
            if attempts < max_attempts:
                # Retry: set back to queued
                await db.execute(
                    "UPDATE jobs SET status = 'queued', error_message = ? WHERE id = ?",
                    (error, job_id),
                )
            else:
                # Final failure
                await db.execute(
                    "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
                    (error, job_id),
                )
                # Also mark wiki as failed
                async with db.execute(
                    "SELECT wiki_id FROM jobs WHERE id = ?", (job_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        await db.execute(
                            "UPDATE wikis SET status = 'failed', error_message = ? WHERE id = ?",
                            (error, row["wiki_id"]),
                        )
            await db.commit()
        finally:
            await db.close()


job_orchestrator = JobOrchestrator()
