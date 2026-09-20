import json
from collections import deque
from typing import Any

from app.config import settings


class RedisEventStream:
    """Small Redis-backed stream with an in-memory fallback.

    V1 keeps the agent service runnable without Redis. When REDIS_URL is set,
    replace the fallback implementation with a real Redis Streams publisher.
    """

    def __init__(self) -> None:
        self._fallback: dict[str, deque[dict[str, Any]]] = {}
        self._redis = None
        if settings.redis_url:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(settings.redis_url)
            except Exception:
                self._redis = None

    async def publish(self, stream: str, event: dict[str, Any]) -> None:
        if self._redis:
            try:
                await self._redis.xadd(stream, {"event": json.dumps(event, default=str)})
                return
            except Exception:
                pass

        queue = self._fallback.setdefault(stream, deque())
        queue.append(event)

    async def replay(self, stream: str) -> list[dict[str, Any]]:
        if self._redis:
            try:
                entries = await self._redis.xrange(stream)
                return [
                    json.loads(entry[1]["event"])
                    for entry in entries
                ]
            except Exception:
                pass

        return [event for event in self._fallback.get(stream, deque())]

    async def snapshot(self, stream: str) -> str:
        if self._redis:
            try:
                entries = await self._redis.xrange(stream)
                return json.dumps(entries, default=str)
            except Exception:
                pass

        return json.dumps(self._fallback.get(stream, deque()), default=str)
