from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import run_migrations
from app.queue.job_queue import job_orchestrator
from app.routers import chat, health, wikis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.wiki_storage_root.mkdir(parents=True, exist_ok=True)
    await run_migrations()
    await job_orchestrator.start()
    yield
    # Shutdown
    await job_orchestrator.stop()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(wikis.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(health.router, prefix="/api")
