from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Sandbox:
    working_dir: str
    sandbox_type: str  # "local" or "daytona"
    _cleanup_path: str | None = None  # for local sandboxes


class SandboxManager:
    """Abstraction over Daytona (production) and local tmpdir (development)."""

    async def create_sandbox(self, github_url: str, branch: str = "main") -> Sandbox:
        if settings.use_daytona:
            return await self._create_daytona_sandbox(github_url, branch)
        return await self._create_local_sandbox(github_url, branch)

    async def destroy_sandbox(self, sandbox: Sandbox) -> None:
        if sandbox.sandbox_type == "daytona":
            await self._destroy_daytona_sandbox(sandbox)
        elif sandbox._cleanup_path:
            import shutil
            shutil.rmtree(sandbox._cleanup_path, ignore_errors=True)
            logger.info(f"Cleaned up local sandbox: {sandbox._cleanup_path}")

    async def _create_local_sandbox(self, github_url: str, branch: str) -> Sandbox:
        tmpdir = tempfile.mkdtemp(prefix="falcon_")
        repo_dir = f"{tmpdir}/repo"

        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "-b", branch, github_url, repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise RuntimeError(f"Git clone failed: {stderr.decode()}")

        logger.info(f"Cloned {github_url} to {repo_dir}")
        return Sandbox(working_dir=repo_dir, sandbox_type="local", _cleanup_path=tmpdir)

    async def _create_daytona_sandbox(self, github_url: str, branch: str) -> Sandbox:
        try:
            from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams

            daytona = Daytona(DaytonaConfig(
                api_key=settings.daytona_api_key,
                api_url=settings.daytona_api_url,
            ))
            sandbox = daytona.create(CreateSandboxParams(
                language="python",
                env_vars={
                    "CODEX_API_KEY": settings.codex_api_key,
                },
                auto_stop_interval=30,
            ))
            # Clone repo inside Daytona sandbox
            response = sandbox.process.exec(
                f"git clone --depth=1 -b {branch} {github_url} /workspace/repo"
            )
            logger.info(f"Cloned {github_url} in Daytona sandbox")
            return Sandbox(
                working_dir="/workspace/repo",
                sandbox_type="daytona",
            )
        except ImportError:
            raise RuntimeError(
                "daytona-sdk not installed. Install with: pip install 'falcon[daytona]'"
            )

    async def _destroy_daytona_sandbox(self, sandbox: Sandbox) -> None:
        # TODO: destroy Daytona workspace via SDK
        logger.info("Daytona sandbox cleanup not yet implemented")
