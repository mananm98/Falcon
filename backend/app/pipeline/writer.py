from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from app.config import settings
from app.sandbox.codex import run_codex
from app.pipeline.prompts import get_writing_prompt
from app.pipeline.agents_md import get_writing_agents_md

logger = logging.getLogger(__name__)


class WikiWriter:
    """Phase 3: Generate wiki pages via Codex CLI."""

    async def write_pages(
        self, working_dir: str, analysis: dict
    ) -> AsyncIterator[str]:
        """Generate wiki pages in dependency waves. Yields page slugs as they complete."""
        # Write AGENTS.md for writing phase
        agents_md_content = get_writing_agents_md()
        agents_md_path = f"{working_dir}/AGENTS.md"
        with open(agents_md_path, "w") as f:
            f.write(agents_md_content)

        # Organize pages into waves
        waves = self._organize_waves(analysis)
        semaphore = asyncio.Semaphore(settings.codex_max_concurrent)

        for wave_name, pages in waves:
            logger.info(f"Generating wave: {wave_name} ({len(pages)} pages)")

            async def generate_page(page: dict) -> str:
                async with semaphore:
                    prompt = get_writing_prompt(page, analysis)
                    result = await run_codex(
                        working_dir=working_dir,
                        prompt=prompt,
                    )
                    if result.exit_code != 0:
                        logger.error(f"Failed to generate page {page['slug']}: {result.stderr}")
                    return page["slug"]

            # Run pages within a wave concurrently
            tasks = [asyncio.create_task(generate_page(p)) for p in pages]
            for task in asyncio.as_completed(tasks):
                slug = await task
                yield slug

    def _organize_waves(self, analysis: dict) -> list[tuple[str, list[dict]]]:
        """Organize pages into generation waves based on dependencies."""
        waves: list[tuple[str, list[dict]]] = []

        # Flatten all pages from sections
        all_pages = []
        for section in analysis.get("sections", []):
            for page in section.get("pages", []):
                page["section"] = section.get("id", "")
                all_pages.append(page)

        # Wave 1: Overview + architecture
        wave1 = [p for p in all_pages if p.get("section") in ("", "architecture")]
        if wave1:
            waves.append(("architecture", wave1))

        # Wave 2: Modules
        wave2 = [p for p in all_pages if p.get("section") == "modules"]
        if wave2:
            waves.append(("modules", wave2))

        # Wave 3: Guides + API reference
        wave3 = [
            p
            for p in all_pages
            if p.get("section") in ("guides", "api-reference")
        ]
        if wave3:
            waves.append(("guides", wave3))

        return waves
