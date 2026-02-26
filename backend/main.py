"""
Falcon — FastAPI application entry point.

Start with:
    uvicorn backend.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db import init_db, close_db
from backend.routers.repos import router as repos_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB pool + schema. Shutdown: close pool."""
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Falcon",
    description="Open-source repo documentation and chat",
    lifespan=lifespan,
)

# CORS — allow Next.js frontend during local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",    # Next.js dev server
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(repos_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
