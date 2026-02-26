"""
Routes for repo management and chat.

POST   /repos                → Ingest a new repo
GET    /repos                → List all repos
GET    /repos/{repo_id}      → Get repo details
DELETE /repos/{repo_id}      → Delete repo + all its files
POST   /repos/{repo_id}/chat → Chat with repo (SSE stream)
"""

import json
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.db import get_conn
from backend.services.ingestion import ingest_repo
from backend.services.agent import run_agent


router = APIRouter(prefix="/repos", tags=["repos"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class IngestRequest(BaseModel):
    url: str


class ChatRequest(BaseModel):
    question: str
    history: list[dict] | None = None


class RepoResponse(BaseModel):
    repo_id: str
    name: str
    url: str
    status: str
    ingested_at: datetime


# ---------------------------------------------------------------------------
# POST /repos — Ingest a new repo
# ---------------------------------------------------------------------------
@router.post("/", status_code=201)
async def create_repo(
    body: IngestRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Clone a git repo, index all files into the database, delete the clone.

    Returns immediately with repo_id and status.
    For large repos, the clone + index might take a few seconds.
    """
    try:
        result = await ingest_repo(conn, body.url)
    except RuntimeError as e:
        # git clone failed (bad URL, private repo, network error)
        raise HTTPException(status_code=400, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# GET /repos — List all repos
# ---------------------------------------------------------------------------
@router.get("/")
async def list_repos(
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT id, name, url, status, ingested_at FROM repos ORDER BY ingested_at DESC"
    )
    return [
        RepoResponse(
            repo_id=str(row["id"]),
            name=row["name"],
            url=row["url"],
            status=row["status"],
            ingested_at=row["ingested_at"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# GET /repos/{repo_id} — Get repo details
# ---------------------------------------------------------------------------
@router.get("/{repo_id}")
async def get_repo(
    repo_id: UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        "SELECT id, name, url, status, ingested_at FROM repos WHERE id = $1",
        repo_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Repo not found")

    # Also get file count for context
    file_count = await conn.fetchval(
        "SELECT count(*) FROM files WHERE repo_id = $1 AND is_directory = false",
        repo_id,
    )

    return {
        "repo_id": str(row["id"]),
        "name": row["name"],
        "url": row["url"],
        "status": row["status"],
        "ingested_at": row["ingested_at"],
        "file_count": file_count,
    }


# ---------------------------------------------------------------------------
# DELETE /repos/{repo_id} — Delete repo and all its files
# ---------------------------------------------------------------------------
@router.delete("/{repo_id}", status_code=204)
async def delete_repo(
    repo_id: UUID,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Deletes the repo row. CASCADE on the FK deletes all file rows too.
    """
    result = await conn.execute("DELETE FROM repos WHERE id = $1", repo_id)

    # result is "DELETE 1" or "DELETE 0"
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Repo not found")


# ---------------------------------------------------------------------------
# POST /repos/{repo_id}/chat — Chat with repo (SSE stream)
# ---------------------------------------------------------------------------
@router.post("/{repo_id}/chat")
async def chat(
    repo_id: UUID,
    body: ChatRequest,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """
    Agentic chat — the LLM explores the repo using tools and streams its answer.

    Returns a Server-Sent Events stream. Each event is a JSON object:
      {"type": "tool_start", "name": "search_code", "arguments": {...}}
      {"type": "tool_end",   "name": "search_code"}
      {"type": "text_delta", "content": "The auth module..."}
      {"type": "done"}
      {"type": "error",      "content": "..."}
    """
    # Verify repo exists and is ready
    row = await conn.fetchrow(
        "SELECT status FROM repos WHERE id = $1", repo_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Repo not found")
    if row["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Repo is not ready (status: {row['status']}). Wait for ingestion to complete.",
        )

    async def event_stream():
        try:
            async for event in run_agent(
                conn=conn,
                repo_id=str(repo_id),
                question=body.question,
                history=body.history,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if behind a proxy
        },
    )
