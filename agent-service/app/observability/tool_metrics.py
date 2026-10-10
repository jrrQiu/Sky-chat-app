"""Tool and agent-loop metrics.

The gap this closes is that a tool call produced an event but no measurement: a report
could say "the model called ``ticket_create``" but not how often tools fail, how long the
ticket service takes, how many retries a flaky dependency costs, or **why** the agent
loop stopped. `/ready` reported the caps; nothing reported the usage against them.

Label discipline is the same as the rest of ``app/observability``: the label set is
closed and contains no user id, no conversation id and no prompt. ``tool`` is a catalog
name, ``outcome`` is one of a fixed set, ``reason`` is a retry or stop classification.

Vocabulary:

=========================== ========= =========================================
name                        type      labels
=========================== ========= =========================================
``tool.calls``              counter   ``tool``, ``outcome``
``tool.latency_ms``         timer     ``tool``, ``outcome``
``tool.retries``            counter   ``name``, ``reason``  (written by resilience)
``tool.circuit_open``       counter   ``tool``             (written by resilience)
``agent_loop.rounds``       timer     ``stop_reason``
``agent_loop.tokens``       counter   ``usage_source``
``agent_loop.stop_reason``  counter   ``reason``
=========================== ========= =========================================
"""

from __future__ import annotations

from typing import Any

from app.observability.registry import metrics
from app.tools import failures

METRIC_TOOL_CALLS = "tool.calls"
METRIC_TOOL_LATENCY = "tool.latency_ms"
METRIC_LOOP_ROUNDS = "agent_loop.rounds"
METRIC_LOOP_TOKENS = "agent_loop.tokens"
METRIC_LOOP_STOP_REASON = "agent_loop.stop_reason"
METRIC_LOOP_DURATION = "agent_loop.duration_ms"

HELP_TOOL_CALLS = "Tool invocations, by tool and outcome."
HELP_TOOL_LATENCY = "Wall-clock duration of tool invocations, in milliseconds."
HELP_LOOP_ROUNDS = "Agent-loop rounds that executed tools, by why the loop stopped."
HELP_LOOP_TOKENS = "Tokens the agent loop accounted for, by whether the provider reported them."
HELP_LOOP_STOP_REASON = "Times the agent loop stopped, by reason."
HELP_LOOP_DURATION = "Wall-clock duration of the agent loop, in milliseconds, by why it stopped."

#: Every outcome a tool call can end in. A closed set keeps the metric readable and
#: stops a caller from inventing a label value that bloats the series list.
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_REFUSED = "refused"
OUTCOME_DENIED = "denied"
OUTCOME_UNAVAILABLE = "unavailable"

STOP_ROUNDS = "ROUNDS"
STOP_TOOL_CALL_BUDGET = "TOOL_CALL_BUDGET"
STOP_TOKEN_BUDGET = "TOKEN_BUDGET"
STOP_WRITE_SUCCEEDED = "WRITE_SUCCEEDED"
STOP_NO_MODEL = "NO_MODEL"
STOP_CONFIRMATION_PENDING = "CONFIRMATION_PENDING"

USAGE_REPORTED = "reported"
USAGE_ESTIMATED = "estimated"


def outcome_for(result: dict[str, Any]) -> str:
    """Classify a tool result envelope into a metric outcome.

    Delegates to :mod:`app.tools.failures`, which is the single table answering both "which
    outcome" and "does this count towards the breaker". The two used to be separate string sets
    that disagreed, so a code missing from both was invisible in the metrics *and* never opened
    the breaker.
    """
    if result.get("success"):
        return OUTCOME_OK
    return failures.outcome_for(result.get("error"))


def record_tool_call(tool: str, *, outcome: str, latency_ms: float) -> None:
    label = tool or "unknown"
    metrics.counter(METRIC_TOOL_CALLS, HELP_TOOL_CALLS, tool=label, outcome=outcome).inc()
    metrics.timer(
        METRIC_TOOL_LATENCY, HELP_TOOL_LATENCY, tool=label, outcome=outcome
    ).observe(max(0.0, float(latency_ms)))


def record_loop_tokens(tokens: int, *, usage_source: str) -> None:
    if tokens <= 0:
        return
    metrics.counter(
        METRIC_LOOP_TOKENS,
        HELP_LOOP_TOKENS,
        usage_source=usage_source if usage_source in {USAGE_REPORTED, USAGE_ESTIMATED} else USAGE_ESTIMATED,
    ).inc(tokens)


def record_loop_stop(reason: str, *, rounds: int, duration_ms: float) -> None:
    """Record why the loop stopped, how many rounds it ran, and how long it took.

    The duration used to be computed by the caller and then dropped on the floor (a dead
    ``... if False else ...`` expression that always recorded the round count). Loop duration
    is the number that explains "the answer was slow because the tools were slow", so it is
    recorded rather than discarded.
    """
    label = reason or "UNKNOWN"
    metrics.counter(METRIC_LOOP_STOP_REASON, HELP_LOOP_STOP_REASON, reason=label).inc()
    metrics.timer(METRIC_LOOP_ROUNDS, HELP_LOOP_ROUNDS, stop_reason=label).observe(
        max(0.0, float(rounds))
    )
    metrics.timer(METRIC_LOOP_DURATION, HELP_LOOP_DURATION, stop_reason=label).observe(
        max(0.0, float(duration_ms))
    )


def tool_metrics_snapshot() -> dict[str, Any]:
    """``count`` and ``mean`` per tool, for the readiness payload.

    Read from the registry rather than kept in a second place, so the endpoint and
    ``/metrics`` cannot disagree.
    """
    snapshot = metrics.snapshot()
    calls = snapshot.get(METRIC_TOOL_CALLS, {})
    latency = snapshot.get(METRIC_TOOL_LATENCY, {})
    by_tool: dict[str, dict[str, Any]] = {}
    for series, value in (calls.get("series") or {}).items():
        labels = dict(
            part.split("=", 1) for part in series.split(",") if "=" in part
        )
        tool = labels.get("tool", "unknown")
        outcome = labels.get("outcome", "unknown")
        entry = by_tool.setdefault(tool, {"calls": {}, "latencyMs": {}})
        entry["calls"][outcome] = value
    for series, value in (latency.get("series") or {}).items():
        labels = dict(
            part.split("=", 1) for part in series.split(",") if "=" in part
        )
        tool = labels.get("tool", "unknown")
        outcome = labels.get("outcome", "unknown")
        entry = by_tool.setdefault(tool, {"calls": {}, "latencyMs": {}})
        entry["latencyMs"][outcome] = {
            "count": (value or {}).get("count", 0),
            "mean": round((value or {}).get("sum", 0.0) / max(1, (value or {}).get("count", 1)), 3),
        }
    stop_reasons = {
        series.split("=", 1)[-1]: value
        for series, value in (snapshot.get(METRIC_LOOP_STOP_REASON, {}).get("series") or {}).items()
    }
    return {"byTool": by_tool, "stopReasons": stop_reasons}
