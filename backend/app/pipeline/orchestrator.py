from __future__ import annotations

import json
import logging
import shutil

from app.config import settings
from app.database import get_db
from app.models import WikiStatus
from app.pipeline.analyzer import RepoAnalyzer
from app.pipeline.writer import WikiWriter
from app.pipeline.indexer import ManifestIndexer
from app.queue.event_bus import event_bus
from app.sandbox.manager import SandboxManager
from app.services.github_service import github_service

logger = logging.getLogger(__name__)


class WikiGenerationPipeline:
    """Orchestrates the full wiki generation pipeline (Phases 1-5)."""

    def __init__(self, wiki_id: str):
        self.wiki_id = wiki_id
        self.sandbox_manager = SandboxManager()
        self.analyzer = RepoAnalyzer()
        self.writer = WikiWriter()
        self.indexer = ManifestIndexer()

    async def execute(self) -> None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT * FROM wikis WHERE id = ?", (self.wiki_id,)
            ) as cursor:
                wiki = await cursor.fetchone()

            if not wiki:
                raise ValueError(f"Wiki {self.wiki_id} not found")

            owner, repo, branch = wiki["owner"], wiki["repo"], wiki["branch"]
            github_url = wiki["github_url"]

            # Phase 1: Repository Acquisition
            await self._update_status(WikiStatus.CLONING)
            await event_bus.publish(self.wiki_id, {"type": "status_change", "data": {"status": "cloning"}})

            sandbox = await self.sandbox_manager.create_sandbox(github_url, branch)

            try:
                # Fetch metadata from GitHub API
                metadata = await github_service.get_repo_metadata(owner, repo)
                await self._update_commit_info(metadata.latest_commit_sha, metadata.languages, metadata.description)

                # Phase 2: Repository Analysis
                await self._update_status(WikiStatus.ANALYZING)
                await event_bus.publish(self.wiki_id, {"type": "status_change", "data": {"status": "analyzing"}})

                analysis = await self.analyzer.analyze(sandbox.working_dir, owner, repo, metadata)
                await self._save_analysis(analysis)

                total_pages = sum(len(s.get("pages", [])) for s in analysis.get("sections", []))
                await self._update_page_counts(total_pages, 0)

                # Phase 3: Wiki Generation
                await self._update_status(WikiStatus.GENERATING)
                await event_bus.publish(self.wiki_id, {"type": "status_change", "data": {"status": "generating"}})

                completed = 0
                async for page_slug in self.writer.write_pages(sandbox.working_dir, analysis):
                    completed += 1
                    await self._update_page_counts(total_pages, completed)
                    await event_bus.publish(
                        self.wiki_id,
                        {
                            "type": "page_complete",
                            "data": {"slug": page_slug, "progress": f"{completed}/{total_pages}"},
                        },
                    )

                # Phase 4: Manifest Generation
                await self._update_status(WikiStatus.INDEXING)
                await event_bus.publish(self.wiki_id, {"type": "status_change", "data": {"status": "indexing"}})

                await self.indexer.generate_manifest(sandbox.working_dir, analysis, metadata)

                # Phase 5: Storage & Completion
                storage_dir = settings.wiki_storage_root / wiki["storage_path"]
                storage_dir.mkdir(parents=True, exist_ok=True)

                await self._copy_wiki_output(sandbox.working_dir, storage_dir)
                await self._populate_page_index(storage_dir)

                await self._update_status(WikiStatus.COMPLETED)
                await event_bus.publish(self.wiki_id, {"type": "complete", "data": {"wiki_id": self.wiki_id}})

            finally:
                await self.sandbox_manager.destroy_sandbox(sandbox)

        finally:
            await db.close()

    async def _update_status(self, status: WikiStatus) -> None:
        db = await get_db()
        try:
            extra = ""
            params: list = [status, self.wiki_id]
            if status == WikiStatus.CLONING:
                extra = ", started_at = datetime('now')"
            elif status == WikiStatus.COMPLETED:
                extra = ", completed_at = datetime('now')"
            await db.execute(
                f"UPDATE wikis SET status = ?, updated_at = datetime('now'){extra} WHERE id = ?",
                params,
            )
            await db.commit()
        finally:
            await db.close()

    async def _update_commit_info(
        self, commit_sha: str, languages: dict, description: str | None
    ) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE wikis SET commit_sha = ?, repo_languages = ?, repo_description = ? WHERE id = ?",
                (commit_sha, json.dumps(languages), description, self.wiki_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def _save_analysis(self, analysis: dict) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE wikis SET analysis_plan = ? WHERE id = ?",
                (json.dumps(analysis), self.wiki_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def _update_page_counts(self, total: int, completed: int) -> None:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE wikis SET total_pages = ?, completed_pages = ? WHERE id = ?",
                (total, completed, self.wiki_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def _copy_wiki_output(self, sandbox_dir: str, storage_dir) -> None:
        """Copy generated wiki files from sandbox to persistent storage."""
        # TODO: implement actual file copy from sandbox wiki output directory
        pass

    async def _populate_page_index(self, storage_dir) -> None:
        """Read manifest and populate wiki_pages + source_file_index tables."""
        # TODO: read manifest.json, insert into wiki_pages and source_file_index
        pass
