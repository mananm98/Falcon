from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class RepoMetadata:
    owner: str
    name: str
    description: str | None
    default_branch: str
    latest_commit_sha: str
    languages: dict[str, float]
    html_url: str


class GitHubService:
    BASE_URL = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_api_token:
            headers["Authorization"] = f"Bearer {settings.github_api_token}"
        return headers

    async def get_repo_metadata(self, owner: str, repo: str) -> RepoMetadata:
        async with httpx.AsyncClient() as client:
            # Fetch repo info
            resp = await client.get(
                f"{self.BASE_URL}/repos/{owner}/{repo}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            # Fetch languages
            lang_resp = await client.get(
                f"{self.BASE_URL}/repos/{owner}/{repo}/languages",
                headers=self._headers(),
            )
            lang_resp.raise_for_status()
            raw_languages = lang_resp.json()

            # Convert bytes to percentages
            total = sum(raw_languages.values()) or 1
            languages = {k: round(v / total * 100, 1) for k, v in raw_languages.items()}

            # Get latest commit SHA
            commits_resp = await client.get(
                f"{self.BASE_URL}/repos/{owner}/{repo}/commits",
                headers=self._headers(),
                params={"per_page": 1, "sha": data["default_branch"]},
            )
            commits_resp.raise_for_status()
            commits = commits_resp.json()
            latest_sha = commits[0]["sha"] if commits else ""

            return RepoMetadata(
                owner=owner,
                name=repo,
                description=data.get("description"),
                default_branch=data["default_branch"],
                latest_commit_sha=latest_sha,
                languages=languages,
                html_url=data["html_url"],
            )


github_service = GitHubService()
