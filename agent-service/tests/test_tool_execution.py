"""Tool execution: heartbeats, result contracts, token accounting, and the write tiers.

These are the parts of the design that are only observable when a real loop runs: whether a
slow tool keeps the turn alive, whether a success-shaped result without an identifier is
allowed through to the model, whether an unreported token count stops bounding the loop, and
whether a `confirm`-tier write really does pause before it leaves the process.

Written as synchronous tests that drive the coroutine with ``asyncio.run`` -- the convention
most of this suite uses, and the one that actually runs in this environment (async test
functions here are silently skipped, which would make every assertion below vacuous).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.graph.nodes import tools as tools_node
from app.observability.registry import MetricsRegistry
from app.tools.calling import ModelTurn, ToolChoice


def state(**overrides):
    value = {
        "request_id": "req-1",
        "turn_id": "turn-1",
        "user_id": "user-1",
        "user_context": {"roles": ["employee"]},
        "selected_agent": "it",
        "intent": "it.incident.report",
        "risk_level": "medium",
        "action": "submit",
        "latest_user_message": "我的打印机坏了",
    }
    value.update(overrides)
    return value


def turn(*calls) -> ModelTurn:
    choices = [
        ToolChoice(name=name, arguments=arguments, call_id=f"call_{index}")
        for index, (name, arguments) in enumerate(calls)
    ]
    return ModelTurn(
        calls=choices,
        assistant_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": choice.call_id,
                    "type": "function",
                    "function": {
                        "name": choice.name,
                        "arguments": json.dumps(choice.arguments),
                    },
                }
                for choice in choices
            ],
        },
    )


def scripted(*turns: ModelTurn):
    queue = list(turns)

    async def fake(_messages, _names, **_kwargs):
        return queue.pop(0) if queue else ModelTurn(calls=[], assistant_message={})

    return fake


class RecordingAdapter:
    """Captures calls and returns a scripted result, optionally slowly."""

    def __init__(self, result=None, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {
            "success": True,
            "text": "已创建工单 #7",
            "data": {"number": 7, "status": "new"},
        }
        self.delay = delay
        self.idempotency_keys: list[str | None] = []

    async def execute(self, tool_name, args, state):
        from app.tools.adapters.contract import current_idempotency_key

        self.calls.append((tool_name, args))
        self.idempotency_keys.append(current_idempotency_key())
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.result)


def collect_events():
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    return events, emit


def run_loop(*, adapter, turns, events_state=None, emit=None):
    """Drive the loop with a stub adapter and a scripted model."""
    tools_node.get_tool_adapter = lambda: adapter
    tools_node.model_turn = scripted(*turns)
    return asyncio.run(tools_node.run_tool_loop(events_state or state(), emit=emit))


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """Undo the monkeypatching these tests do by assignment and clear the ledger.

    The effect ledger is a deliberate process-wide singleton (a write must be deduplicated
    across every caller in the process), so without this each test would inherit the previous
    one's ``turn + action`` rows -- and a duplicate-looking write would be legitimately
    deduplicated, making the test fail for the right reason at the wrong time.
    """
    from app.core.effect_ledger import effect_ledger
    from app.tools.resilience import reset_breakers

    original_adapter = tools_node.get_tool_adapter
    original_turn = tools_node.model_turn
    original_registry = tools_node.default_registry
    effect_ledger.reset()
    reset_breakers()
    yield
    tools_node.get_tool_adapter = original_adapter
    tools_node.model_turn = original_turn
    tools_node.default_registry = original_registry
    effect_ledger.reset()
    reset_breakers()


# ------------------------------------------------------------------ heartbeats
def test_a_slow_tool_emits_heartbeats_instead_of_silence(monkeypatch):
    """The 25-second tool that used to look like a hung stream."""
    from app.config import settings

    adapter = RecordingAdapter(delay=0.2)
    monkeypatch.setattr(settings, "tool_heartbeat_seconds", 0.05, raising=False)
    events, emit = collect_events()

    run_loop(adapter=adapter, turns=[turn(("ticket_list", {}))], emit=emit)

    progress = [event for event in events if event.get("type") == "tool_progress"]
    assert progress, "a slow tool must not be silent"
    assert all(item.get("toolCallId") for item in progress)
    assert progress[0]["progress"] is None
    assert "still running" in progress[0]["note"]


def test_a_fast_tool_emits_no_heartbeat(monkeypatch):
    from app.config import settings

    adapter = RecordingAdapter()
    monkeypatch.setattr(settings, "tool_heartbeat_seconds", 10.0, raising=False)
    events, emit = collect_events()

    run_loop(adapter=adapter, turns=[turn(("ticket_list", {}))], emit=emit)

    assert not [event for event in events if event.get("type") == "tool_progress"]


# ------------------------------------------------------------ result contract
def test_a_success_without_the_declared_field_is_rewritten_to_a_failure():
    """`ticket_create` returning success without a number is the input that invites invention."""
    violated = tools_node.apply_result_contract(
        "ticket_create", {"success": True, "text": "已创建工单", "data": {"status": "new"}}
    )
    assert violated["success"] is False
    assert violated["error"] == tools_node.RESULT_SCHEMA_VIOLATION
    assert violated["missingFields"] == ["data.number"]
    assert "请勿据此推断编号" in violated["text"]


def test_a_complete_result_is_untouched():
    ok = {"success": True, "text": "已创建工单 #7", "data": {"number": 7}}
    assert tools_node.apply_result_contract("ticket_create", ok) == ok


def test_a_failure_is_not_reinterpreted():
    """Only a *successful* result is checked: a failure already says nothing happened."""
    failed = {
        "success": False,
        "text": "工单服务暂时不可用",
        "error": "TICKET_SERVICE_UNAVAILABLE",
    }
    assert tools_node.apply_result_contract("ticket_create", failed) == failed


def test_the_loop_reports_a_schema_violation_to_the_model(monkeypatch):
    """A success-shaped result missing its identifier must not reach the model as a success.

    The confirmation tier is disabled here on purpose: `ticket_create` is `confirm`, so with
    confirmation on the call never reaches the adapter and there would be no result to check.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)
    adapter = RecordingAdapter(result={"success": True, "text": "done", "data": {}})
    events, emit = collect_events()

    results, _mode, _rounds, _accounting = run_loop(
        adapter=adapter,
        turns=[turn(("ticket_create", {"title": "打印机坏了"}))],
        emit=emit,
    )

    assert results[0]["success"] is False
    assert results[0]["error"] == tools_node.RESULT_SCHEMA_VIOLATION
    reported = [event for event in events if event.get("type") == "tool_result"]
    assert reported[-1]["errorCode"] == tools_node.RESULT_SCHEMA_VIOLATION


# --------------------------------------------------------------- token budget
def test_usage_reporting_adds_only_the_new_tokens():
    """Summing `total_tokens` across rounds re-counts the prompt and trips the budget early."""
    messages = [{"role": "user", "content": "x"}]
    first = ModelTurn(
        calls=[],
        assistant_message={},
        usage={"promptTokens": 100, "completionTokens": 10, "totalTokens": 110},
    )
    second = ModelTurn(
        calls=[],
        assistant_message={},
        usage={"promptTokens": 150, "completionTokens": 20, "totalTokens": 170},
    )

    total, source = tools_node._account_tokens(first, messages, 0)
    assert total == 110 and source == "reported"
    total, source = tools_node._account_tokens(second, messages, total)
    assert total == 110 + 170, "input+output of the second turn only"
    assert source == "reported"


def test_unreported_usage_advances_the_budget_with_an_estimate():
    """`tokens += usage.get('totalTokens', 0)` made the budget inert when usage was absent."""
    messages = [{"role": "user", "content": "我的打印机坏了" * 20}]
    turn_without_usage = ModelTurn(calls=[], assistant_message={}, usage=None)

    total, source = tools_node._account_tokens(turn_without_usage, messages, 0)
    assert total > 0, "an unreported count must not be treated as zero"
    assert source == "estimated"


def test_the_budget_stops_the_loop_even_without_provider_usage(monkeypatch):
    from app.config import settings

    adapter = RecordingAdapter()
    monkeypatch.setattr(settings, "agent_token_budget", 1, raising=False)
    events, emit = collect_events()

    results, _mode, _rounds, accounting = run_loop(
        adapter=adapter,
        turns=[turn(("ticket_list", {})), turn(("ticket_list", {}))],
        emit=emit,
    )

    assert accounting["stopReason"] == "TOKEN_BUDGET"
    assert accounting["usageSource"] == "estimated"
    assert results == [], "the budget stopped the loop before the first call"


# ------------------------------------------------------------------ T7 tiers
def test_a_confirm_tier_write_pauses_without_touching_the_adapter():
    adapter = RecordingAdapter()
    events, emit = collect_events()

    results, _mode, _rounds, accounting = run_loop(
        adapter=adapter,
        turns=[turn(("ticket_create", {"title": "打印机坏了"}))],
        emit=emit,
    )

    assert adapter.calls == [], "a confirm-tier write must not execute in the loop"
    assert len(accounting["pendingConfirmWrites"]) == 1
    pending = accounting["pendingConfirmWrites"][0]
    assert pending["name"] == "ticket_create"
    assert pending["callId"] == "call_0"
    assert results[0]["error"] == "CONFIRMATION_REQUIRED"
    assert accounting["stopReason"] == "CONFIRMATION_PENDING"


def test_confirm_can_be_turned_off_and_then_the_write_runs(monkeypatch):
    from app.config import settings

    adapter = RecordingAdapter()
    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)

    results, _mode, _rounds, accounting = run_loop(
        adapter=adapter,
        turns=[turn(("ticket_create", {"title": "打印机坏了"}))],
    )

    assert [name for name, _args in adapter.calls] == ["ticket_create"]
    assert accounting["pendingConfirmWrites"] == []
    assert results[0]["success"] is True


def test_a_read_only_tool_is_never_paused():
    adapter = RecordingAdapter(
        result={"success": True, "text": "共 1 张工单", "data": {"total": 1}}
    )

    results, _mode, _rounds, accounting = run_loop(
        adapter=adapter, turns=[turn(("ticket_list", {}))]
    )

    assert [name for name, _args in adapter.calls] == ["ticket_list"]
    assert accounting["pendingConfirmWrites"] == []
    assert results[0]["success"] is True


def test_a_write_carries_the_ledger_key_to_the_adapter(monkeypatch):
    """T3: the provider key has to reach the adapter, or Java cannot deduplicate."""
    from app.config import settings

    adapter = RecordingAdapter()
    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)

    run_loop(
        adapter=adapter,
        turns=[turn(("ticket_comment", {"number": 7, "body": "补充信息"}))],
    )

    assert adapter.calls, "the write did not run"
    assert adapter.idempotency_keys[0], "no idempotency key was bound for the write"
    assert str(adapter.idempotency_keys[0]).startswith("idem_")


# ---------------------------------------------------------------- loop metrics
def test_the_stop_reason_is_reported_as_a_metric(monkeypatch):
    adapter = RecordingAdapter()
    local = MetricsRegistry()
    monkeypatch.setattr(tools_node.tool_metrics, "metrics", local)
    events, emit = collect_events()

    run_loop(adapter=adapter, turns=[turn(("ticket_list", {}))], emit=emit)

    series = (
        local.snapshot().get(tools_node.tool_metrics.METRIC_LOOP_STOP_REASON, {}) or {}
    ).get("series") or {}
    assert series, "no stop reason was recorded"
    assert any("COMPLETED" in key for key in series)


def test_a_tool_call_is_recorded_with_an_outcome(monkeypatch):
    adapter = RecordingAdapter(
        result={"success": False, "text": "不可用", "error": "TOOL_PROVIDER_UNAVAILABLE"}
    )
    local = MetricsRegistry()
    monkeypatch.setattr(tools_node.tool_metrics, "metrics", local)

    run_loop(adapter=adapter, turns=[turn(("ticket_list", {}))])

    calls = (
        local.snapshot().get(tools_node.tool_metrics.METRIC_TOOL_CALLS, {}) or {}
    ).get("series") or {}
    assert any("unavailable" in key for key in calls), calls


def test_an_open_breaker_refuses_locally_without_calling_the_adapter(monkeypatch):
    from app.tools import resilience

    adapter = RecordingAdapter()
    registry = resilience.BreakerRegistry(failure_threshold=1, cooldown_seconds=60)
    registry.breaker("ticket_list").record_failure("boom", transport=True)
    monkeypatch.setattr(tools_node, "default_registry", lambda: registry)

    results, _mode, _rounds, _accounting = run_loop(
        adapter=adapter, turns=[turn(("ticket_list", {}))]
    )

    assert adapter.calls == [], "the breaker must stop the network call"
    assert results[0]["error"] == tools_node.TOOL_CIRCUIT_OPEN
