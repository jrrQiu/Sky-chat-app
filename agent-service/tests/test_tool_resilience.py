"""Retry boundaries (T1/T2) and the circuit breaker.

The rule the tests exist to defend is T1: **a write is retried only when the request never
reached the peer**. Getting that wrong creates a duplicate ticket, and the failure mode is
invisible in exactly the situation retries are for (a flaky network), which is why the
boundary is asserted rather than described.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.observability.registry import MetricsRegistry
from app.tools import resilience
from app.tools.resilience import (
    BreakerRegistry,
    CircuitBreaker,
    RetryPolicy,
    call_with_retry,
    is_connection_failure,
    is_safe_to_retry,
    retry_reason,
    status_code_of,
)


class Boom(Exception):
    def __init__(self, message: str = "boom", status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("connection refused")


def _read_timeout() -> httpx.ReadTimeout:
    return httpx.ReadTimeout("timed out")


# ------------------------------------------------------------------ T1 boundary
def test_a_write_is_only_retried_when_the_request_never_landed():
    assert is_safe_to_retry(_connect_error(), writes=True) is True
    assert is_safe_to_retry(_read_timeout(), writes=True) is False
    # A 5xx may well mean the write was applied before the response was lost.
    assert is_safe_to_retry(Boom(status_code=503), writes=True) is False
    assert is_safe_to_retry(Boom(status_code=429), writes=True) is False


def test_a_read_retries_transient_failures_but_not_deterministic_ones():
    assert is_safe_to_retry(_read_timeout(), writes=False) is True
    assert is_safe_to_retry(_connect_error(), writes=False) is True
    assert is_safe_to_retry(Boom(status_code=503), writes=False) is True
    assert is_safe_to_retry(Boom(status_code=429), writes=False) is True
    # 400/403/404 are answers, not hiccups: retrying only burns the budget.
    assert is_safe_to_retry(Boom(status_code=400), writes=False) is False
    assert is_safe_to_retry(Boom(status_code=403), writes=False) is False


def test_write_retry_executes_exactly_twice_on_a_connection_error():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise _connect_error()

    async def scenario():
        policy = RetryPolicy(
            max_attempts=2, initial_backoff_seconds=0, total_budget_seconds=5, writes=True
        )
        with pytest.raises(httpx.ConnectError):
            await call_with_retry(attempt, policy, name="ticket_create")

    asyncio.run(scenario())
    assert attempts["count"] == 2, "a write retries once on a connection failure"


def test_write_is_not_retried_after_a_read_timeout():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise _read_timeout()

    async def scenario():
        policy = RetryPolicy(
            max_attempts=3, initial_backoff_seconds=0, total_budget_seconds=5, writes=True
        )
        with pytest.raises(httpx.ReadTimeout):
            await call_with_retry(attempt, policy, name="ticket_create")

    asyncio.run(scenario())
    assert attempts["count"] == 1, "a sent write is never repeated"


def test_read_retries_up_to_the_attempt_cap():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise _read_timeout()

    async def scenario():
        policy = RetryPolicy(
            max_attempts=3, initial_backoff_seconds=0, total_budget_seconds=5
        )
        with pytest.raises(httpx.ReadTimeout):
            await call_with_retry(attempt, policy, name="ticket_list")

    asyncio.run(scenario())
    assert attempts["count"] == 3


# ------------------------------------------------------------------ T2 budget
def test_retry_stops_when_the_total_budget_is_spent():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise _read_timeout()

    async def scenario():
        policy = RetryPolicy(
            max_attempts=10,
            initial_backoff_seconds=0.05,
            backoff_factor=4.0,
            max_backoff_seconds=1.0,
            jitter=0,
            total_budget_seconds=0.12,
        )
        started = asyncio.get_running_loop().time()
        with pytest.raises(httpx.ReadTimeout):
            await call_with_retry(attempt, policy, name="ticket_list")
        return asyncio.get_running_loop().time() - started

    elapsed = asyncio.run(scenario())
    # The waits sum to about 0.05 + 0.2 (capped by the budget), so it must stop well short
    # of "10 attempts × 1s".
    assert elapsed < 1.0
    assert attempts["count"] < 10


def test_backoff_grows_and_is_jittered():
    policy = RetryPolicy(
        initial_backoff_seconds=0.2,
        backoff_factor=3.0,
        max_backoff_seconds=10.0,
        jitter=0.0,
    )
    assert policy.backoff_for(1) == pytest.approx(0.2)
    assert policy.backoff_for(2) == pytest.approx(0.6)
    assert policy.backoff_for(3) == pytest.approx(1.8)

    jittered = RetryPolicy(
        initial_backoff_seconds=1.0, max_backoff_seconds=1.0, jitter=0.25
    )
    values = {round(jittered.backoff_for(1), 4) for _ in range(20)}
    assert len(values) > 1, "jitter must actually vary the wait"
    assert all(0.75 <= value <= 1.25 for value in values)


def test_retry_is_reported_on_the_outcome():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise Boom(status_code=503)
        return "ok"

    async def scenario():
        policy = RetryPolicy(max_attempts=3, initial_backoff_seconds=0, total_budget_seconds=5)
        return await call_with_retry(attempt, policy, name="model.tool_choice")

    outcome = asyncio.run(scenario())
    assert outcome.value == "ok"
    assert outcome.attempts == 2
    assert outcome.reasons == ["server_error"]
    assert outcome.retried is True


def test_cancellation_is_not_retried():
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise asyncio.CancelledError()

    async def scenario():
        policy = RetryPolicy(max_attempts=3, initial_backoff_seconds=0, total_budget_seconds=5)
        with pytest.raises(asyncio.CancelledError):
            await call_with_retry(attempt, policy, name="anything")

    asyncio.run(scenario())
    assert attempts["count"] == 1


def test_status_code_is_found_through_a_wrapped_exception():
    """A 429 hidden inside a service-level exception must still be retried."""
    inner = Boom(status_code=429)
    outer = RuntimeError("tool calling failed")
    outer.__cause__ = inner
    assert status_code_of(outer) == 429
    assert retry_reason(outer) == "rate_limited"


def test_retry_metrics_use_a_label_name_that_does_not_collide():
    """`name` is the metric name parameter, so the label must not be called `name`."""
    attempts = {"count": 0}

    async def attempt():
        attempts["count"] += 1
        raise Boom(status_code=503)

    async def scenario():
        policy = RetryPolicy(max_attempts=2, initial_backoff_seconds=0, total_budget_seconds=5)
        with pytest.raises(Boom):
            await call_with_retry(attempt, policy, name="ticket_list")

    asyncio.run(scenario())
    series = resilience.metrics.snapshot().get(resilience.METRIC_RETRIES, {})
    keys = list(series.get("series") or {})
    assert keys, "the retry was not recorded"
    assert any("call=" in key for key in keys)


# ------------------------------------------------------------------ breaker
def test_breaker_opens_after_consecutive_transport_failures():
    clock = {"now": 1000.0}
    breaker = CircuitBreaker(
        "ticket_create", failure_threshold=3, cooldown_seconds=30, clock=lambda: clock["now"]
    )
    assert breaker.is_open() is False
    for _ in range(3):
        breaker.record_failure(_connect_error(), transport=True)
    assert breaker.is_open() is True

    # Cooldown elapses: the breaker half-opens and lets a probe through.
    clock["now"] += 31
    assert breaker.is_open() is False

    # A failed probe re-opens it immediately (the failure count is still at the threshold).
    breaker.record_failure(_connect_error(), transport=True)
    assert breaker.is_open() is True

    breaker.record_success()
    assert breaker.is_open() is False
    assert breaker.failures == 0


def test_breaker_ignores_business_failures():
    """A 4xx means the dependency answered: it is healthy, and the fault is ours."""
    breaker = CircuitBreaker("ticket_list", failure_threshold=2)
    for _ in range(5):
        assert breaker.record_failure(Boom(status_code=403), transport=False) is False
    assert breaker.is_open() is False


def test_breaker_registry_is_per_tool():
    registry = BreakerRegistry(failure_threshold=1, cooldown_seconds=60)
    registry.breaker("ticket_create").record_failure("x", transport=True)
    assert registry.open_breakers() == ["ticket_create"]
    assert registry.breaker("ticket_list").is_open() is False

    described = registry.describe()
    assert described["open"] == ["ticket_create"]
    registry.reset()
    assert registry.open_breakers() == []


def test_breaker_metric_is_recorded_when_it_opens():
    local = MetricsRegistry()
    original = resilience.metrics
    resilience.metrics = local
    try:
        breaker = CircuitBreaker("ticket_create", failure_threshold=1)
        breaker.record_failure(_connect_error(), transport=True)
        assert (
            local.counter_value(resilience.METRIC_CIRCUIT_OPEN, tool="ticket_create") == 1.0
        )
    finally:
        resilience.metrics = original


def test_connection_failure_classification_is_narrow():
    assert is_connection_failure(_connect_error()) is True
    assert is_connection_failure(httpx.ConnectTimeout("x")) is True
    assert is_connection_failure(_read_timeout()) is False
    assert is_connection_failure(Boom(status_code=503)) is False
