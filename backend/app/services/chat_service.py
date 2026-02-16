from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

from app.config import settings
from app.database import get_db
from app.models import ConversationMessage
from app.services.wiki_service import wiki_service


class ChatService:
    async def handle_message(
        self,
        wiki_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Handle a chat message and yield SSE events."""
        # Ensure wiki exists and is completed
        wiki = await wiki_service.get_wiki(wiki_id)
        if not wiki:
            yield {"type": "error", "data": {"message": "Wiki not found"}}
            return

        # Get or create conversation
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
            await self._create_conversation(wiki_id, conversation_id)

        # Save user message
        await self._save_message(conversation_id, "user", message)

        # Select context pages from manifest
        manifest = await wiki_service.get_manifest(wiki_id)
        context_slugs = []
        if manifest:
            context_slugs = self._select_context_pages(manifest, message)

        yield {"type": "thinking", "data": {"context_pages": context_slugs}}

        # TODO: Invoke Codex CLI for Q&A with wiki context + source access
        # For now, placeholder response
        response = f"[Q&A not yet implemented] You asked about: {message}"

        yield {
            "type": "complete",
            "data": {
                "response": response,
                "sources": context_slugs,
                "conversation_id": conversation_id,
            },
        }

        # Save assistant message
        await self._save_message(
            conversation_id, "assistant", response, context_pages=context_slugs
        )

    def _select_context_pages(
        self, manifest: dict, question: str, max_pages: int = 5
    ) -> list[str]:
        """Score and rank wiki pages by relevance to the question."""
        question_lower = question.lower()
        question_terms = set(question_lower.split())
        scored: list[tuple[str, float]] = []

        for page in manifest.get("pages", []):
            score = 0.0

            # Title match
            title_terms = set(page.get("title", "").lower().split())
            overlap = question_terms & title_terms
            if overlap:
                score += 3.0 * len(overlap) / max(len(question_terms), 1)

            # Summary match
            summary_terms = set(page.get("summary", "").lower().split())
            overlap = question_terms & summary_terms
            if overlap:
                score += 2.0 * len(overlap) / max(len(question_terms), 1)

            # Key exports exact match
            for export in page.get("key_exports", []):
                if export.lower() in question_lower:
                    score += 5.0

            # Source file name match
            for f in page.get("source_files", []):
                filename = f.split("/")[-1].replace("_", " ").replace(".py", "")
                if any(term in filename.lower() for term in question_terms):
                    score += 2.0

            if score > 0:
                scored.append((page["slug"], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [slug for slug, _ in scored[:max_pages]]

    async def _create_conversation(self, wiki_id: str, conversation_id: str) -> None:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO conversations (id, wiki_id) VALUES (?, ?)",
                (conversation_id, wiki_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def _save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        context_pages: list[str] | None = None,
    ) -> None:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, context_pages) VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    role,
                    content,
                    json.dumps(context_pages) if context_pages else None,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_conversation(
        self, wiki_id: str, conversation_id: str
    ) -> list[ConversationMessage] | None:
        db = await get_db()
        try:
            async with db.execute(
                "SELECT id FROM conversations WHERE id = ? AND wiki_id = ?",
                (conversation_id, wiki_id),
            ) as cursor:
                if not await cursor.fetchone():
                    return None

            async with db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    ConversationMessage(
                        id=r["id"],
                        role=r["role"],
                        content=r["content"],
                        context_pages=json.loads(r["context_pages"])
                        if r["context_pages"]
                        else None,
                        created_at=r["created_at"],
                    )
                    for r in rows
                ]
        finally:
            await db.close()


chat_service = ChatService()
