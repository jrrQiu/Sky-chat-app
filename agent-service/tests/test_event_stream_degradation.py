"""The event stream must degrade quickly and visibly when Redis is unavailable."""

import asyncio
import logging

import pytest

from app.config import settings
from app.persistence.redis_streams import RedisEventStream


class BrokenRedis:
    """Stands in for a Redis that accepts the call and then times out."""

    def __init__(self) -> None:
        self.attempts = 0

    async def xadd(self, *_args, **_kwargs):
        self.attempts += 1
        raise TimeoutError("Timeout connecting to server")

    async def xrange(self, *_args, **_kwargs):
        self.attempts += 1
        raise TimeoutError("Timeout connecting to server")


@pytest.fixture
def stream(monkeypatch):
    instance = RedisEventStream.__new__(RedisEventStream)
    instance._fallback = {}
    instance._redis = BrokenRedis()
    instance._failures = 0
    instance._degraded_until = 0.0
    instance._degraded_logged = False
    return instance


def test_stream_falls_back_to_the_buffer_and_stops_calling_redis(stream, caplog):
    async def scenario():
        limit = settings.redis_degrade_after_failures
        for index in range(limit + 5):
            await stream.publish("run:1", {"type": "answer", "index": index})

        # Redis was attempted only until the breaker opened.
        assert stream._redis.attempts == limit
        assert stream.degraded is True

        buffered = await stream.replay("run:1")
        assert len(buffered) == limit + 5
        assert buffered[0] == {"type": "answer", "index": 0}

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    assert any("skipping Redis" in record.message for record in caplog.records)
    assert any("Redis publish failed" in record.message for record in caplog.records)


def test_stream_recovers_after_the_cool_down(stream):
    async def scenario():
        for _ in range(settings.redis_degrade_after_failures):
            await stream.publish("run:2", {"type": "answer"})
        assert stream.degraded is True

        # Simulate the cool-down elapsing, then a healthy Redis.
        stream._degraded_until = 0.0

        async def healthy_xadd(*_args, **_kwargs):
            return "1-1"

        stream._redis.xadd = healthy_xadd
        await stream.publish("run:2", {"type": "answer"})
        assert stream.degraded is False
        assert stream._failures == 0

    asyncio.run(scenario())


def test_disabled_redis_is_not_an_error():
    instance = RedisEventStream.__new__(RedisEventStream)
    instance._fallback = {}
    instance._redis = None
    instance._failures = 0
    instance._degraded_until = 0.0
    instance._degraded_logged = False

    async def scenario():
        await instance.publish("run:3", {"type": "complete"})
        assert await instance.replay("run:3") == [{"type": "complete"}]
        assert instance.degraded is True

    asyncio.run(scenario())
