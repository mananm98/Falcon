from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CodexResult:
    exit_code: int
    events: list[dict] = field(default_factory=list)
    output: str = ""
    stderr: str = ""


def _extract_final_message(events: list[dict]) -> str:
    """Extract the final agent message from Codex JSON Lines events."""
    for event in reversed(events):
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("item_type") == "message":
                return item.get("text", "")
    # Fallback: concatenate all message text
    parts = []
    for event in events:
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if "text" in item:
                parts.append(item["text"])
    return "\n".join(parts)


async def run_codex(
    working_dir: str,
    prompt: str,
    output_schema_path: str | None = None,
    timeout_seconds: int | None = None,
) -> CodexResult:
    """Invoke Codex CLI in non-interactive mode and parse JSON Lines output."""
    if timeout_seconds is None:
        timeout_seconds = settings.codex_timeout_seconds

    cmd = [
        "codex", "exec",
        "--json",
        "--full-auto",
        "--sandbox", "workspace-write",
    ]
    if output_schema_path:
        cmd.extend(["--output-schema", output_schema_path])
    cmd.append(prompt)

    env = {**os.environ}
    if settings.codex_api_key:
        env["CODEX_API_KEY"] = settings.codex_api_key

    logger.info(f"Running Codex in {working_dir}: {prompt[:100]}...")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        events: list[dict] = []

        async def read_stdout():
            assert process.stdout
            async for line in process.stdout:
                line_str = line.decode().strip()
                if not line_str:
                    continue
                try:
                    event = json.loads(line_str)
                    events.append(event)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON Codex output: {line_str}")

        try:
            await asyncio.wait_for(read_stdout(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            logger.error(f"Codex timed out after {timeout_seconds}s")
            return CodexResult(exit_code=-1, events=events, stderr="Timeout")

        stderr_bytes = await process.stderr.read() if process.stderr else b""
        await process.wait()

        result = CodexResult(
            exit_code=process.returncode or 0,
            events=events,
            output=_extract_final_message(events),
            stderr=stderr_bytes.decode(),
        )

        logger.info(f"Codex finished with exit code {result.exit_code}")
        return result

    except FileNotFoundError:
        raise RuntimeError(
            "Codex CLI not found. Install with: npm install -g @openai/codex"
        )
