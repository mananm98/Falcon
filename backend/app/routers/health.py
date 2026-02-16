from fastapi import APIRouter

from app.config import settings
from app.models import HealthResponse
from app.queue.job_queue import job_orchestrator

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        active_jobs=len(job_orchestrator.active_jobs),
    )
