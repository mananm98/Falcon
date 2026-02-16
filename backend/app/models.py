from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, HttpUrl


# --- Enums ---


class WikiStatus(StrEnum):
    QUEUED = "queued"
    CLONING = "cloning"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --- Request Models ---


class CreateWikiRequest(BaseModel):
    github_url: HttpUrl
    branch: str = "main"


class ChatMessageRequest(BaseModel):
    message: str
    conversation_id: str | None = None


# --- Response Models ---


class WikiResponse(BaseModel):
    wiki_id: str
    owner: str
    repo: str
    github_url: str
    branch: str
    commit_sha: str | None
    status: WikiStatus
    total_pages: int
    completed_pages: int
    created_at: str
    completed_at: str | None


class WikiStatusResponse(BaseModel):
    status: WikiStatus
    phase: str | None = None
    progress: ProgressInfo | None = None
    started_at: str | None = None
    elapsed_seconds: float | None = None


class ProgressInfo(BaseModel):
    completed: int
    total: int
    current_page: str | None = None


class PageSummary(BaseModel):
    slug: str
    title: str
    section: str
    order: int
    summary: str | None = None


class PageDetail(BaseModel):
    slug: str
    title: str
    section: str
    content_md: str
    frontmatter: dict


class ChatResponse(BaseModel):
    response: str
    sources: list[str]
    conversation_id: str


class ConversationMessage(BaseModel):
    id: str
    role: str
    content: str
    context_pages: list[str] | None = None
    created_at: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    active_jobs: int
