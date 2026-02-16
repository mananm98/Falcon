from __future__ import annotations

import logging

from app.sandbox.codex import run_codex
from app.pipeline.prompts import get_analysis_prompt
from app.pipeline.agents_md import get_analysis_agents_md
from app.services.github_service import RepoMetadata

logger = logging.getLogger(__name__)


class RepoAnalyzer:
    """Phase 2: Analyze repository structure via Codex CLI."""

    async def analyze(
        self,
        working_dir: str,
        owner: str,
        repo: str,
        metadata: RepoMetadata,
    ) -> dict:
        # Write AGENTS.md for analysis phase
        agents_md_content = get_analysis_agents_md()
        agents_md_path = f"{working_dir}/AGENTS.md"
        with open(agents_md_path, "w") as f:
            f.write(agents_md_content)

        # Build the analysis prompt
        prompt = get_analysis_prompt(owner, repo, metadata)

        # Invoke Codex
        result = await run_codex(
            working_dir=working_dir,
            prompt=prompt,
        )

        if result.exit_code != 0:
            raise RuntimeError(f"Codex analysis failed: {result.stderr}")

        # Parse structured output
        # TODO: parse the Codex output into a structured analysis dict
        # For now, return placeholder
        return {
            "repository": {"owner": owner, "name": repo},
            "sections": [],
            "modules": [],
        }
