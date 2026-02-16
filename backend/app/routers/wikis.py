import re
import uuid

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models import (
    CreateWikiRequest,
    PageDetail,
    PageSummary,
    WikiResponse,
    WikiStatus,
    WikiStatusResponse,
    ProgressInfo,
)
from app.queue.event_bus import event_bus
from app.services.wiki_service import wiki_service

router = APIRouter(tags=["wikis"])


def _parse_github_url(url: str) -> tuple[str, str]:
    """Extract owner and repo from a GitHub URL."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", str(url))
    if not match:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL")
    return match.group(1), match.group(2)


@router.post("/wikis")
async def create_wiki(request: CreateWikiRequest) -> dict:
    owner, repo = _parse_github_url(str(request.github_url))
    wiki_id = await wiki_service.create_wiki(
        owner=owner,
        repo=repo,
        github_url=str(request.github_url),
        branch=request.branch,
    )
    return {"wiki_id": wiki_id, "status": "queued"}


@router.get("/wikis/{wiki_id}")
async def get_wiki(wiki_id: str) -> WikiResponse:
    wiki = await wiki_service.get_wiki(wiki_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found")
    return wiki


@router.get("/wikis/{wiki_id}/manifest")
async def get_manifest(wiki_id: str) -> dict:
    manifest = await wiki_service.get_manifest(wiki_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return manifest


@router.get("/wikis/{wiki_id}/pages")
async def list_pages(wiki_id: str) -> list[PageSummary]:
    return await wiki_service.list_pages(wiki_id)


@router.get("/wikis/{wiki_id}/pages/{slug:path}")
async def get_page(wiki_id: str, slug: str) -> PageDetail:
    page = await wiki_service.get_page(wiki_id, slug)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.delete("/wikis/{wiki_id}", status_code=204)
async def delete_wiki(wiki_id: str) -> None:
    await wiki_service.delete_wiki(wiki_id)


@router.get("/wikis")
async def find_wikis(
    owner: str = Query(default=None),
    repo: str = Query(default=None),
) -> list[WikiResponse]:
    return await wiki_service.find_wikis(owner=owner, repo=repo)


@router.get("/wikis/{wiki_id}/status")
async def get_wiki_status(wiki_id: str) -> WikiStatusResponse:
    status = await wiki_service.get_status(wiki_id)
    if not status:
        raise HTTPException(status_code=404, detail="Wiki not found")
    return status


@router.get("/wikis/{wiki_id}/events")
async def wiki_events(wiki_id: str):
    """SSE endpoint for real-time wiki generation progress."""

    async def event_generator():
        queue = event_bus.subscribe(wiki_id)
        try:
            while True:
                event = await queue.get()
                yield {
                    "event": event["type"],
                    "data": event.get("data", {}),
                }
                if event["type"] in ("complete", "error"):
                    break
        finally:
            event_bus.unsubscribe(wiki_id, queue)

    return EventSourceResponse(event_generator())
