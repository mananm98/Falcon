from __future__ import annotations

import json
import uuid
from pathlib import Path

import frontmatter

from app.config import settings
from app.database import get_db
from app.models import (
    PageDetail,
    PageSummary,
    ProgressInfo,
    WikiResponse,
    WikiStatus,
    WikiStatusResponse,
)


class WikiService:
    async def create_wiki(
        self, owner: str, repo: str, github_url: str, branch: str
    ) -> str:
        db = await get_db()
        try:
            # Check for existing wiki
            async with db.execute(
                "SELECT id, status FROM wikis WHERE owner = ? AND repo = ? AND branch = ?",
                (owner, repo, branch),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["status"] == WikiStatus.COMPLETED:
                    return row["id"]

            wiki_id = str(uuid.uuid4())
            storage_path = f"{owner}/{repo}/{wiki_id}"

            await db.execute(
                """INSERT INTO wikis (id, owner, repo, github_url, branch, status, storage_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (wiki_id, owner, repo, github_url, branch, WikiStatus.QUEUED, storage_path),
            )
            await db.execute(
                """INSERT INTO jobs (id, job_type, wiki_id, status)
                   VALUES (?, 'wiki_generation', ?, 'queued')""",
                (str(uuid.uuid4()), wiki_id),
            )
            await db.commit()
            return wiki_id
        finally:
            await db.close()

    async def get_wiki(self, wiki_id: str) -> WikiResponse | None:
        db = await get_db()
        try:
            async with db.execute("SELECT * FROM wikis WHERE id = ?", (wiki_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return WikiResponse(
                    wiki_id=row["id"],
                    owner=row["owner"],
                    repo=row["repo"],
                    github_url=row["github_url"],
                    branch=row["branch"],
                    commit_sha=row["commit_sha"],
                    status=row["status"],
                    total_pages=row["total_pages"],
                    completed_pages=row["completed_pages"],
                    created_at=row["created_at"],
                    completed_at=row["completed_at"],
                )
        finally:
            await db.close()

    async def get_manifest(self, wiki_id: str) -> dict | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT storage_path FROM wikis WHERE id = ? AND status = ?",
                (wiki_id, WikiStatus.COMPLETED),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

            manifest_path = settings.wiki_storage_root / row["storage_path"] / "manifest.json"
            if not manifest_path.exists():
                return None
            return json.loads(manifest_path.read_text())
        finally:
            await db.close()

    async def list_pages(self, wiki_id: str) -> list[PageSummary]:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT slug, title, section, sort_order, summary FROM wiki_pages WHERE wiki_id = ? ORDER BY sort_order",
                (wiki_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    PageSummary(
                        slug=r["slug"],
                        title=r["title"],
                        section=r["section"],
                        order=r["sort_order"],
                        summary=r["summary"],
                    )
                    for r in rows
                ]
        finally:
            await db.close()

    async def get_page(self, wiki_id: str, slug: str) -> PageDetail | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT w.storage_path, p.file_path FROM wikis w JOIN wiki_pages p ON w.id = p.wiki_id WHERE w.id = ? AND p.slug = ?",
                (wiki_id, slug),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

            file_path = settings.wiki_storage_root / row["storage_path"] / row["file_path"]
            if not file_path.exists():
                return None

            post = frontmatter.load(str(file_path))
            return PageDetail(
                slug=slug,
                title=post.metadata.get("title", ""),
                section=post.metadata.get("section", ""),
                content_md=post.content,
                frontmatter=dict(post.metadata),
            )
        finally:
            await db.close()

    async def get_status(self, wiki_id: str) -> WikiStatusResponse | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT status, total_pages, completed_pages, started_at, created_at FROM wikis WHERE id = ?",
                (wiki_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                progress = None
                if row["total_pages"] > 0:
                    progress = ProgressInfo(
                        completed=row["completed_pages"],
                        total=row["total_pages"],
                    )

                return WikiStatusResponse(
                    status=row["status"],
                    progress=progress,
                    started_at=row["started_at"],
                )
        finally:
            await db.close()

    async def delete_wiki(self, wiki_id: str) -> None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT storage_path FROM wikis WHERE id = ?", (wiki_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if row and row["storage_path"]:
                wiki_dir = settings.wiki_storage_root / row["storage_path"]
                if wiki_dir.exists():
                    import shutil
                    shutil.rmtree(wiki_dir)

            await db.execute("DELETE FROM wikis WHERE id = ?", (wiki_id,))
            await db.commit()
        finally:
            await db.close()

    async def find_wikis(
        self, owner: str | None = None, repo: str | None = None
    ) -> list[WikiResponse]:
        db = await get_db()
        try:
            query = "SELECT * FROM wikis WHERE 1=1"
            params: list = []
            if owner:
                query += " AND owner = ?"
                params.append(owner)
            if repo:
                query += " AND repo = ?"
                params.append(repo)
            query += " ORDER BY created_at DESC"

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [
                    WikiResponse(
                        wiki_id=r["id"],
                        owner=r["owner"],
                        repo=r["repo"],
                        github_url=r["github_url"],
                        branch=r["branch"],
                        commit_sha=r["commit_sha"],
                        status=r["status"],
                        total_pages=r["total_pages"],
                        completed_pages=r["completed_pages"],
                        created_at=r["created_at"],
                        completed_at=r["completed_at"],
                    )
                    for r in rows
                ]
        finally:
            await db.close()


wiki_service = WikiService()
