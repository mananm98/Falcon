from __future__ import annotations

import json
import logging

from app.config import settings
from app.sandbox.codex import run_codex
from app.pipeline.prompts import get_indexing_prompt
from app.services.github_service import RepoMetadata

logger = logging.getLogger(__name__)


class ManifestIndexer:
    """Phase 4: Generate manifest.json from completed wiki pages."""

    async def generate_manifest(
        self,
        working_dir: str,
        analysis: dict,
        metadata: RepoMetadata,
    ) -> dict:
        prompt = get_indexing_prompt(analysis, metadata)

        result = await run_codex(
            working_dir=working_dir,
            prompt=prompt,
        )

        if result.exit_code != 0:
            logger.error(f"Manifest generation failed: {result.stderr}")
            # Fall back to building manifest from analysis plan
            return self._build_fallback_manifest(analysis, metadata)

        # TODO: parse Codex output into manifest dict and write to file
        return self._build_fallback_manifest(analysis, metadata)

    def _build_fallback_manifest(
        self, analysis: dict, metadata: RepoMetadata
    ) -> dict:
        """Build a basic manifest from the analysis plan when Codex fails."""
        return {
            "version": "1.0",
            "repository": {
                "owner": metadata.owner,
                "name": metadata.name,
                "url": metadata.html_url,
                "default_branch": metadata.default_branch,
                "commit_sha": metadata.latest_commit_sha,
                "languages": metadata.languages,
                "description": metadata.description,
            },
            "falcon_version": settings.app_version,
            "sections": analysis.get("sections", []),
            "pages": [],
            "source_index": {},
            "graph": {"nodes": [], "edges": []},
            "stats": {
                "total_pages": 0,
                "total_source_files_covered": 0,
                "total_source_files_in_repo": 0,
                "coverage_percent": 0.0,
            },
        }
