"""Retry, backoff and circuit breaking for tool and model calls.

The gap this closes: every outbound call in the tool layer was a single attempt. One
429 from the model turned a model-driven tool decision into a rule-based one, and one
connection blip made the ticket tool answer "服务暂时不可用" for something that would
have worked on the next try.

Three rules shape the code, and the first is the one that matters:

**T1 — retry only when we know nothing happened.** A connection that was never
established, a connection refused, or a DNS failure means the request never landed, so
repeating it cannot repeat an effect. Once a request has been sent, a read timeout or an
unparseable response leaves the outcome *unknown*: a write may well have been applied.
Writes are therefore never retried after the request left the process; the effect ledger
and the provider-side idempotency key decide whether the effect happened.

**T2 — retries have a total budget.** The waits count against a per-call ceiling, so
"three attempts each" cannot silently become a minute of accumulated latency. A retry
that would exceed the budget is not attempted and the caller gets the original error.

**T8 — every retry is attributable.** :func:`call_with_retry` returns the attempt count
and the reason each retry happened, which the caller puts in the tool result, the event
and the metrics. A silent retry hides the fact that a dependency is degrading.

The classification is deliberately the same as the Java side's
``AgentCircuitBreaker.isSafeToRetry`` (Java retries only idempotent GETs on transport
errors); two different answers to "is this safe to retry" in one system is how a
duplicate write eventually happens.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from app.observability.registry import metrics

logger = logging.getLogger(__name__)

T = TypeVar("T")

METRIC_RETRIES = "tool.retries"
METRIC_CIRCUIT_OPEN = "tool.circuit_open"
HELP_RETRIES = "Tool and model calls retried, by name and the reason for the retry."
HELP_CIRCUIT_OPEN = "Calls refused locally because a tool's circuit breaker was open."

#: Error codes surfaced to the caller. `TOOL_PROVIDER_UNAVAILABLE` matches the code the
#: adapters already use for "the target system could not be reached", so an open breaker
#: and an unreachable system are the same thing from the model's point of view.
ERROR_PROVIDER_UNAVAILABLE = "TOOL_PROVIDER_UNAVAILABLE"
#: Reserved: when the retry budget is spent, ``call_with_retry`` re-raises the original error
#: rather than wrapping it, so this code is not currently put on a result envelope. Kept so the
#: distinction it names ("we stopped retrying early") is not lost.
ERROR_RETRY_BUDGET_EXHAUSTED = "TOOL_RETRY_BUDGET_EXHAUSTED"

#: HTTP statuses that mean "the service is up but asked us to back off". A 4xx that is not
#: 429 is a deterministic refusal: retrying it only wastes the budget.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """Bound on attempts, latency and which failures may be retried."""

    #: Total attempts including the first one. 1 disables retrying.
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.2
    backoff_factor: float = 3.0
    max_backoff_seconds: float = 2.0
    #: Fraction of the backoff added or subtracted at random, so a fleet of retrying
    #: callers does not synchronise into a thundering herd.
    jitter: float = 0.25
    #: Ceiling on the sum of the waits for one call. 0 disables the check.
    total_budget_seconds: float = 8.0
    #: When true the caller is a write, which narrows what may be retried (T1).
    writes: bool = False

    def backoff_for(self, attempt: int) -> float:
        """Wait before attempt number ``attempt + 1`` (attempt is 1-based)."""
        raw = self.initial_backoff_seconds * (self.backoff_factor ** max(0, attempt - 1))
        capped = min(raw, self.max_backoff_seconds)
        if self.jitter <= 0:
            return capped
        spread = capped * self.jitter
        return max(0.0, capped + random.uniform(-spread, spread))


@dataclass
class RetryOutcome:
    """Result of a retried call, with the evidence a report needs."""

    value: T
    attempts: int
    reasons: list[str] = field(default_factory=list)
    waived: str = ""

    @property
    def retried(self) -> bool:
        return self.attempts > 1

    @property
    def retry_reasons(self) -> list[str]:
        return list(self.reasons)


def _transport_kind(error: BaseException) -> str:
    """Classify a transport failure without losing the concrete type name."""
    name = type(error).__name__
    if isinstance(error, httpx.ConnectError):
        return f"ConnectError/{name}" if name != "ConnectError" else "ConnectError"
    if isinstance(error, httpx.ConnectTimeout):
        return "ConnectTimeout"
    if isinstance(error, httpx.ReadTimeout):
        return "ReadTimeout"
    if isinstance(error, httpx.WriteTimeout):
        return "WriteTimeout"
    if isinstance(error, httpx.PoolTimeout):
        return "PoolTimeout"
    if isinstance(error, httpx.RemoteProtocolError):
        return "RemoteProtocolError"
    if isinstance(error, httpx.TimeoutException):
        return f"Timeout/{name}"
    if isinstance(error, httpx.TransportError):
        return f"TransportError/{name}"
    return name


def is_connection_failure(error: BaseException) -> bool:
    """True when the request demonstrably never reached the peer.

    This is the *only* category a write may be retried in: nothing can have been applied
    because the connection was never established.
    """
    return isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout))


def is_transient(error: BaseException) -> bool:
    """True for failures a read-only call may safely repeat."""
    if is_connection_failure(error):
        return True
    if isinstance(error, httpx.TransportError):
        # Read/Write/Pool timeouts and protocol errors: the request may have landed, which
        # is fine for a read and not fine for a write.
        return True
    status = status_code_of(error)
    return status is not None and status in RETRYABLE_STATUS_CODES


def status_code_of(error: BaseException) -> int | None:
    """The HTTP status an exception carries, if any.

    Checked on the exception itself and then on its cause/context, because the adapters
    wrap a status in a service-level exception. Without the walk, a 429 hidden inside a
    ``FunctionCallingUnavailable`` would look deterministic and never be retried.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if status is None:
            response = getattr(current, "response", None)
            status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
        nxt = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        current = nxt if isinstance(nxt, BaseException) else None
    return None


def retry_reason(error: BaseException) -> str:
    """Short, low-cardinality label for why a retry happened (a metric label)."""
    if is_connection_failure(error):
        return "connect"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.TransportError):
        return "transport"
    status = status_code_of(error)
    if status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "server_error"
    if status is not None:
        return f"status_{status}"
    return "transient"


def is_safe_to_retry(error: BaseException, *, writes: bool) -> bool:
    """T1 in one function: may this call be repeated without risking a second effect?"""
    if writes:
        return is_connection_failure(error)
    return is_transient(error)


async def call_with_retry(
    call: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    name: str = "call",
    on_retry: Callable[[int, float, str], Awaitable[None] | None] | None = None,
) -> RetryOutcome[T]:
    """Run ``call`` under ``policy``.

    ``on_retry(attempt, wait_seconds, reason)`` is awaited when it returns an awaitable,
    so a caller can emit a progress event between attempts.

    Raises the last exception when every attempt failed, or the first one immediately when
    it is not safe to retry. A failure that is *not* retryable is raised unchanged rather
    than wrapped: the caller's error handling already knows what to do with it.
    """
    attempts = 0
    reasons: list[str] = []
    spent_waiting = 0.0
    max_attempts = max(1, int(policy.max_attempts))

    while attempts < max_attempts:
        attempts += 1
        try:
            value = await call()
            return RetryOutcome(value=value, attempts=attempts, reasons=reasons)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Cancellation is not a failure to retry: the caller is going away, and
            # retrying would keep doing work nobody is waiting for any more.
            raise
        except Exception as error:
            safe = is_safe_to_retry(error, writes=policy.writes)
            last_attempt = attempts >= max_attempts
            if not safe or last_attempt:
                raise
            wait = policy.backoff_for(attempts)
            if policy.total_budget_seconds and (
                spent_waiting + wait > policy.total_budget_seconds
            ):
                # T2: the retry budget is spent. Report the original failure and why the
                # retry did not happen, instead of silently waiting longer.
                logger.warning(
                    "%s: retry budget exhausted after %s attempt(s); not retrying %s",
                    name,
                    attempts,
                    type(error).__name__,
                )
                raise
            reason = retry_reason(error)
            reasons.append(reason)
            # The label is `call`, not `name`: `MetricsRegistry.counter(name, help, **labels)`
            # already has a parameter called `name`, and passing a label with that name raises
            # "got multiple values for argument 'name'" at the call site — which is exactly how
            # this first showed up, as a failed tool call rather than a failed metric.
            metrics.counter(
                METRIC_RETRIES, HELP_RETRIES, call=name, reason=reason
            ).inc()
            logger.info(
                "%s: attempt %s failed (%s); retrying in %.2fs",
                name,
                attempts,
                type(error).__name__,
                wait,
            )
            if on_retry is not None:
                produced = on_retry(attempts, wait, reason)
                if asyncio.iscoroutine(produced):
                    await produced
            await asyncio.sleep(wait)
            spent_waiting += wait

    # Unreachable: the loop either returns or raises.
    raise RuntimeError(f"{name}: retry loop exited without a result")


@dataclass
class BreakerState:
    failures: int = 0
    opened_at: float = 0.0
    opened_count: int = 0
    last_error: str = ""


class CircuitBreaker:
    """Per-tool breaker: stop calling a system that is consistently unreachable.

    Only *transport* failures count. An HTTP 4xx means the service answered, so it is
    healthy and the problem is ours; counting it would open the breaker because of a
    caller bug. This is the same rule the Java ``AgentCircuitBreaker`` uses, and the
    reason both sides state it is so the two cannot drift.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self._state = BreakerState()
        # Re-entrant, because the internal helpers lock too (`describe` reports `open` by
        # asking `_is_open_locked`). A plain Lock deadlocked the whole request thread here --
        # found by the test suite hanging rather than failing, which is exactly how a lock bug
        # usually presents itself.
        self._lock = threading.RLock()

    @property
    def failures(self) -> int:
        return self._state.failures

    def is_open(self) -> bool:
        with self._lock:
            return self._is_open_locked()

    def _is_open_locked(self) -> bool:
        if self._state.failures < self.failure_threshold:
            return False
        elapsed = self._clock() - self._state.opened_at
        if elapsed >= self.cooldown_seconds:
            # Half-open: the next call is allowed through as a probe. It is not a
            # success, so a failing probe re-opens immediately.
            return False
        return True

    def record_success(self) -> None:
        with self._lock:
            self._state.failures = 0
            self._state.opened_at = 0.0
            self._state.last_error = ""

    def record_failure(self, error: BaseException | str, *, transport: bool) -> bool:
        """Record a failure. Returns True when this failure opened the breaker."""
        if not transport:
            return False
        with self._lock:
            self._state.failures += 1
            self._state.last_error = type(error).__name__ if isinstance(error, BaseException) else str(error)
            if self._state.failures >= self.failure_threshold:
                self._state.opened_at = self._clock()
                self._state.opened_count += 1
                metrics.counter(
                    METRIC_CIRCUIT_OPEN, HELP_CIRCUIT_OPEN, tool=self.name
                ).inc()
                return True
        return False

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tool": self.name,
                "failures": self._state.failures,
                "threshold": self.failure_threshold,
                "cooldownSeconds": self.cooldown_seconds,
                "open": self._is_open_locked(),
                "openedCount": self._state.opened_count,
                "lastError": self._state.last_error,
            }

    def reset(self) -> None:
        with self._lock:
            self._state = BreakerState()


class BreakerRegistry:
    """Breakers by name. One per process, because the state is about the dependency."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def breaker(self, name: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(
                    name,
                    failure_threshold=self._threshold,
                    cooldown_seconds=self._cooldown,
                    clock=self._clock,
                )
                self._breakers[name] = breaker
            return breaker

    def open_breakers(self) -> list[str]:
        with self._lock:
            names = list(self._breakers)
        return [name for name in names if self._breakers[name].is_open()]

    def describe(self) -> dict[str, Any]:
        with self._lock:
            names = sorted(self._breakers)
            described = [self._breakers[name].describe() for name in names]
        return {
            "threshold": self._threshold,
            "cooldownSeconds": self._cooldown,
            # Honest about the limit: the counters live in this process, so with N replicas the
            # effective threshold is N x failure_threshold, and a restart clears the state. A
            # shared store (Redis, as the rate limiter uses) is the fix. Saying nothing here
            # would let an operator read "threshold: 3" and believe it is global.
            "scope": "per-process",
            "open": [item["tool"] for item in described if item["open"]],
            "tools": described,
        }

    def reset(self) -> None:
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


def default_registry() -> BreakerRegistry:
    """The process-wide registry, built from settings on first use."""
    from app.config import settings

    global _registry
    if _registry is None:
        _registry = BreakerRegistry(
            failure_threshold=settings.tool_circuit_failure_threshold,
            cooldown_seconds=settings.tool_circuit_cooldown_seconds,
        )
    return _registry


_registry: BreakerRegistry | None = None


def reset_breakers() -> None:
    """Drop the process-wide breaker state. For tests and for an operator reset."""
    registry = default_registry()
    registry.reset()


def policy_for_tool(*, writes: bool) -> RetryPolicy:
    """The retry policy a tool call runs under, from settings."""
    from app.config import settings

    return RetryPolicy(
        max_attempts=(
            settings.tool_retry_write_attempts if writes else settings.tool_retry_read_attempts
        ),
        initial_backoff_seconds=settings.tool_retry_initial_backoff_seconds,
        backoff_factor=settings.tool_retry_backoff_factor,
        max_backoff_seconds=settings.tool_retry_max_backoff_seconds,
        jitter=settings.tool_retry_jitter,
        total_budget_seconds=settings.tool_retry_total_budget_seconds,
        writes=writes,
    )


def policy_for_model_turn() -> RetryPolicy:
    """The policy a tool-selection model turn runs under.

    The call is read-only in effect (it chooses, it does not change anything), so
    transient failures are retried; a deterministic 4xx is not.
    """
    from app.config import settings

    return RetryPolicy(
        max_attempts=3,
        initial_backoff_seconds=0.2,
        backoff_factor=3.0,
        max_backoff_seconds=2.0,
        jitter=0.25,
        total_budget_seconds=min(8.0, settings.llm_timeout_seconds / 3),
        writes=False,
    )


def describe_resilience() -> dict[str, Any]:
    """Reporting view for ``/ready``: what is enforced, and what is currently open."""
    from app.config import settings

    return {
        "retry": {
            "readAttempts": settings.tool_retry_read_attempts,
            "writeAttempts": settings.tool_retry_write_attempts,
            "totalBudgetSeconds": settings.tool_retry_total_budget_seconds,
            "callDeadlineSeconds": settings.tool_call_deadline_seconds,
            "backoffSeconds": {
                "initial": settings.tool_retry_initial_backoff_seconds,
                "factor": settings.tool_retry_backoff_factor,
                "max": settings.tool_retry_max_backoff_seconds,
                "jitter": settings.tool_retry_jitter,
            },
        },
        "writeRetryRule": "connection-not-established only (T1)",
        "circuitBreaker": default_registry().describe(),
    }
