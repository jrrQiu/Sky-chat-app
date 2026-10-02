import json
import logging
import time
from collections import deque
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class RedisEventStream:
    """Redis Streams publisher with a bounded-degradation in-process fallback.

    A Redis outage must never be paid for on every event: connect attempts use a
    short timeout, and after a few consecutive failures the client is parked for
    a cool-down window so the stream keeps flowing from the local buffer while a
    single warning records why.
    """

    def __init__(self) -> None:
        self._fallback: dict[str, deque[dict[str, Any]]] = {}
        self._redis = None
        self._failures = 0
        self._degraded_until = 0.0
        self._degraded_logged = False

        if settings.redis_url:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=settings.redis_connect_timeout_seconds,
                    socket_timeout=settings.redis_socket_timeout_seconds,
                )
            except Exception:
                logger.warning(
                    "Redis client could not be created; using the in-process buffer",
                    exc_info=True,
                )
                self._redis = None

    @property
    def degraded(self) -> bool:
        """True while Redis is skipped because of recent failures."""
        return self._redis is None or time.monotonic() < self._degraded_until

    def _available(self) -> bool:
        if self._redis is None:
            return False
        if time.monotonic() < self._degraded_until:
            return False
        return True

    def _record_success(self) -> None:
        if self._failures or self._degraded_until:
            logger.info("Redis event stream recovered")
        self._failures = 0
        self._degraded_until = 0.0
        self._degraded_logged = False

    def _record_failure(self, operation: str, exc: Exception) -> None:
        self._failures += 1
        if self._failures < settings.redis_degrade_after_failures:
            logger.warning("Redis %s failed: %s", operation, exc)
            return
        self._degraded_until = time.monotonic() + settings.redis_degrade_seconds
        if not self._degraded_logged:
            self._degraded_logged = True
            logger.warning(
                "Redis %s failed %d times; skipping Redis for %.0fs and using the "
                "in-process buffer",
                operation,
                self._failures,
                settings.redis_degrade_seconds,
            )

    async def publish(self, stream: str, event: dict[str, Any]) -> None:
        if self._available():
            try:
                await self._redis.xadd(
                    stream,
                    {"event": json.dumps(event, default=str)},
                )
                self._record_success()
                return
            except Exception as exc:
                self._record_failure("publish", exc)

        queue = self._fallback.setdefault(stream, deque())
        queue.append(event)

    async def replay(self, stream: str) -> list[dict[str, Any]]:
        if self._available():
            try:
                entries = await self._redis.xrange(stream)
                self._record_success()
                return [json.loads(entry[1]["event"]) for entry in entries]
            except Exception as exc:
                self._record_failure("replay", exc)

        return [event for event in self._fallback.get(stream, deque())]

    async def snapshot(self, stream: str) -> str:
        if self._available():
            try:
                entries = await self._redis.xrange(stream)
                self._record_success()
                return json.dumps(entries, default=str)
            except Exception as exc:
                self._record_failure("snapshot", exc)

        return json.dumps(self._fallback.get(stream, deque()), default=str)
