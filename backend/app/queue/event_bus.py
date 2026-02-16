from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """In-memory pub/sub for real-time wiki generation progress events."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def publish(self, wiki_id: str, event: dict) -> None:
        subscribers = self._subscribers.get(wiki_id, [])
        for queue in subscribers:
            await queue.put(event)
        logger.debug(f"Published {event.get('type')} to {len(subscribers)} subscribers for {wiki_id}")

    def subscribe(self, wiki_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(wiki_id, []).append(queue)
        logger.debug(f"New subscriber for {wiki_id}")
        return queue

    def unsubscribe(self, wiki_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(wiki_id, [])
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(wiki_id, None)


event_bus = EventBus()
