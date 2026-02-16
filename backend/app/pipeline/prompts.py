from __future__ import annotations

from app.services.github_service import RepoMetadata


def get_analysis_prompt(owner: str, repo: str, metadata: RepoMetadata) -> str:
    return f"""Analyze this repository and produce a documentation plan.
The repo is {owner}/{repo}: {metadata.description or 'No description'}.
Primary languages: {', '.join(metadata.languages.keys())}.

Focus on identifying:
1. The main modules and their boundaries
2. How modules depend on each other
3. Key public APIs and entry points
4. What documentation sections and pages would best explain this codebase

Read the top-level files first (README, config files), then explore each major directory.

Output a JSON object with this structure:
{{
  "modules": [
    {{"name": "...", "directory": "...", "purpose": "...", "key_files": [...], "depends_on": [...]}}
  ],
  "sections": [
    {{
      "id": "architecture",
      "title": "Architecture",
      "order": 1,
      "description": "...",
      "pages": [
        {{
          "slug": "architecture/system-design",
          "title": "System Design",
          "source_files": [...],
          "source_dirs": [...],
          "summary": "..."
        }}
      ]
    }}
  ],
  "entry_points": [...],
  "config_files": [...]
}}"""


def get_writing_prompt(page: dict, analysis: dict) -> str:
    file_list = "\n".join(f"  - {f}" for f in page.get("source_files", []))
    other_pages = [
        f"  - {p['slug']}: {p.get('title', '')}"
        for s in analysis.get("sections", [])
        for p in s.get("pages", [])
        if p["slug"] != page["slug"]
    ]
    other_pages_str = "\n".join(other_pages[:20])

    return f"""Write the wiki page "{page.get('title', page['slug'])}" for section "{page.get('section', '')}".

This page should cover these source files:
{file_list}

Summary of what to cover: {page.get('summary', 'See source files')}

Other wiki pages that exist (for cross-references):
{other_pages_str}

Write the documentation as a markdown file with YAML frontmatter.
The frontmatter must include: title, slug, section, order, source_files, source_dirs,
depends_on, depended_by, key_exports, module_type, languages, complexity, generated_at.

Focus on explaining the architecture, key functions, data flow, and usage patterns.
Include actual code snippets from the source files.
Include Mermaid diagrams where they help explain relationships or flows.
Target 500-1500 words."""


def get_indexing_prompt(analysis: dict, metadata: RepoMetadata) -> str:
    return f"""Read all the generated wiki markdown files in the current directory and produce a manifest.json file.

The manifest should contain:
1. Repository info: owner={metadata.owner}, name={metadata.name}, url={metadata.html_url},
   default_branch={metadata.default_branch}, commit_sha={metadata.latest_commit_sha}
2. A "sections" array listing all wiki sections
3. A "pages" array with every page's slug, title, section, file_path, summary, source_files, key_exports, depends_on
4. A "source_index" mapping each source file path to the wiki page slugs that cover it
5. A "graph" with nodes (pages) and edges (depends_on relationships)
6. A "stats" object with total_pages, total_source_files_covered, and coverage_percent

Write the manifest.json file to the current directory."""
