"""Fixed-window rate limiting with a Redis backend and an in-process fallback.

Redis is the correct backend for a multi-replica deployment. When it is
unavailable the limiter degrades to per-process counters: that keeps the service
available (a rate limiter must not become a single point of failure for login
and chat) but it is explicitly **not** a cross-replica guarantee, which is why
the degradation is logged and the check is skipped entirely when the service
cannot count at all.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.core.security import Caller, require_caller

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter:
    def __init__(self) -> None:
        self._redis: Any = None
        self._redis_checked = False
        self._memory: dict[str, tuple[int, float]] = {}
        self._degraded_logged = False

    def _client(self) -> Any:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        if not settings.redis_url:
            return None
        try:
            import redis.asyncio as redis

            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=settings.redis_connect_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
            )
        except Exception:
            logger.warning("Rate limiter has no Redis client", exc_info=True)
            self._redis = None
        return self._redis

    def _memory_hit(self, bucket_key: str, limit: int, ttl: float) -> RateLimitResult:
        now = time.monotonic()
        count, expires_at = self._memory.get(bucket_key, (0, now + ttl))
        if expires_at <= now:
            count, expires_at = 0, now + ttl
        count += 1
        self._memory[bucket_key] = (count, expires_at)
        if len(self._memory) > 10_000:  # opportunistic cleanup
            self._memory = {
                key: value for key, value in self._memory.items() if value[1] > now
            }
        return RateLimitResult(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=max(1, int(expires_at - now)),
        )

    async def check(
        self,
        scope: str,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        if not settings.rate_limit_enabled or limit <= 0:
            return RateLimitResult(True, limit, limit, 0)

        bucket = int(time.time() // window_seconds)
        bucket_key = f"{settings.rate_limit_prefix}:{scope}:{key}:{bucket}"
        client = self._client()

        if client is not None:
            try:
                count = await client.incr(bucket_key)
                if count == 1:
                    await client.expire(bucket_key, window_seconds + 5)
                ttl = await client.ttl(bucket_key)
                return RateLimitResult(
                    allowed=count <= limit,
                    limit=limit,
                    remaining=max(0, limit - count),
                    retry_after=max(1, ttl if ttl and ttl > 0 else window_seconds),
                )
            except Exception as exc:
                if not self._degraded_logged:
                    self._degraded_logged = True
                    logger.warning(
                        "Rate limiter degraded to per-process counters: %s", exc
                    )
                self._redis = None
                self._redis_checked = True

        if settings.rate_limit_fail_open:
            return self._memory_hit(bucket_key, limit, float(window_seconds))
        return RateLimitResult(False, limit, 0, window_seconds)


rate_limiter = RateLimiter()


def _identity(caller: Caller, request: Request, key: str) -> str:
    if key == "ip":
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded and getattr(settings, "trust_forwarded_for", False):
            return forwarded.split(",")[0].strip()
        return caller.source_ip or (request.client.host if request.client else "unknown")
    return caller.user_id or "anonymous"


def enforce_rate_limit(
    scope: str,
    per_minute: int,
    key: str = "user",
    window_seconds: int = 60,
):
    """Build a FastAPI dependency that rejects the caller when over the limit."""

    async def dependency(
        request: Request,
        caller: Caller = Depends(require_caller),
    ) -> Caller:
        result = await rate_limiter.check(
            scope,
            _identity(caller, request, key),
            per_minute,
            window_seconds,
        )
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="RATE_LIMITED",
                headers={"Retry-After": str(result.retry_after)},
            )
        return caller

    return dependency
