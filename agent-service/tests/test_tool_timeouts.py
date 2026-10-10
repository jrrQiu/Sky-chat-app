"""Timeout, retry and fallback wiring the guards are supposed to guarantee.

Each test here pins a defect that was reachable in production while the *mechanism* looked
correct in isolation:

* a tool call had no wall-clock cap at all. httpx's timeout is per operation, and its read
  timeout measures the gap *between* reads, so a dependency that drips bytes could hold the
  node for its entire run budget while every other guard reported it healthy;
* the retry policy could never see an HTTP status: httpx does not raise for 4xx/5xx, so a 429
  or a 503 came back as a "successful" call and was never retried, even though
  ``RETRYABLE_STATUS_CODES`` lists it;
* a ``NO_MODEL`` stop skipped the rule-based fallback, so a deployment without a model ran no
  tools at all while the log claimed "using rules";
* the node's idle window was a constant the readiness endpoint did not validate, and the node's
  run budget omitted the retries one call may spend.

They are integration tests on purpose: the unit tests for the retry classifier passed
throughout, because the classifier was right and the wiring was not.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core.effect_ledger import EffectLedger, lease_seconds
from app.graph import root as root_module
from app.graph.nodes import tools as tools_node
from app.observability import tool_metrics
from app.observability.registry import metrics
from app.tools.adapters.ticket import TicketToolAdapter
from app.tools.calling import FunctionCallingUnavailable, ModelTurn, ToolChoice


def state(**overrides):
    value = {
        "request_id": "req-timeout",
        "turn_id": "turn-timeout",
        "user_id": "user-1",
        "user_context": {"roles": ["employee"]},
        "selected_agent": "it",
        "intent": "it.incident.report",
        "risk_level": "medium",
        "action": "query",
        "latest_user_message": "我的打印机坏了",
    }
    value.update(overrides)
    return value


def turn(*calls) -> ModelTurn:
    choices = [
        ToolChoice(name=name, arguments=arguments, call_id=f"call_{index}")
        for index, (name, arguments) in enumerate(calls)
    ]
    return ModelTurn(calls=choices, assistant_message={"role": "assistant"})


def scripted(*turns: ModelTurn):
    queue = list(turns)

    async def fake(_messages, _names, **_kwargs):
        if queue:
            return queue.pop(0)
        return ModelTurn(calls=[], assistant_message={"role": "assistant"})

    return fake


class Hanging:
    """An adapter that accepts the call and never answers."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls: list[str] = []

    async def execute(self, tool_name, args, state):  # noqa: ARG002 - adapter signature
        self.calls.append(tool_name)
        self.started.set()
        await asyncio.sleep(30)
        return {"success": True, "text": "never reached"}


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch):
    """Keep the effect ledger off the database, as the other loop tests do."""
    monkeypatch.setattr(tools_node, "effect_ledger", EffectLedger(postgres=None))
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: Hanging())
    yield


@pytest.fixture(autouse=True)
def _no_confirmation_pause(monkeypatch):
    """Both write tools are `confirm`-tier; the tier itself is covered elsewhere."""
    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)
    yield


# ------------------------------------------------------------------ the per-call deadline


@pytest.mark.asyncio
async def test_a_hanging_read_is_cut_off_by_the_deadline(monkeypatch):
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 0.05, raising=False)
    monkeypatch.setattr(tools_node, "model_turn", scripted(turn(("ticket_list", {}))))

    results, _mode, _rounds, _accounting = await tools_node.run_tool_loop(state())

    assert results[0]["success"] is False
    assert results[0]["error"] == tools_node.TOOL_CALL_TIMEOUT
    assert "未执行" in results[0]["text"]


@pytest.mark.asyncio
async def test_a_timed_out_dependency_counts_towards_the_breaker(monkeypatch):
    from app.tools.resilience import default_registry

    default_registry().reset()
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 0.05, raising=False)
    monkeypatch.setattr(tools_node, "model_turn", scripted(turn(("ticket_list", {}))))

    await tools_node.run_tool_loop(state())

    assert default_registry().breaker("ticket_list").failures >= 1


@pytest.mark.asyncio
async def test_a_hanging_write_reports_an_unknown_outcome(monkeypatch):
    """A write that times out must not look like a clean refusal: it may have been applied."""
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 0.05, raising=False)
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_create", {"title": "打印机坏了"})))
    )

    results, _mode, _rounds, _accounting = await tools_node.run_tool_loop(state())

    assert results[0]["error"] == tools_node.RESULT_UNKNOWN
    assert "结果未知" in results[0]["text"]
    assert "请勿重复提交" in results[0]["text"]


@pytest.mark.asyncio
async def test_cancelling_an_in_flight_write_announces_an_unknown_outcome(monkeypatch):
    """Cancellation is the other path where a write's outcome cannot be confirmed."""
    adapter = Hanging()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 30.0, raising=False)
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_create", {"title": "打印机坏了"})))
    )
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    task = asyncio.create_task(tools_node.run_tool_loop(state(), emit=emit))
    await asyncio.wait_for(adapter.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    unknown = [event for event in events if event.get("errorCode") == "RESULT_UNKNOWN"]
    assert unknown, events
    assert "请勿重复提交" in unknown[0]["message"]


# ------------------------------------------------------- retryable HTTP statuses (the wiring)


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "busy"

    def json(self):  # pragma: no cover - only reached on the success path
        return {}


class _AlwaysStatus:
    """An httpx-shaped client that answers the same status every time."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.attempts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ARG002 - context manager protocol
        return False

    async def post(self, *args, **kwargs):  # noqa: ARG002 - client protocol
        self.attempts += 1
        return _Response(self.status_code)


def _ticket_adapter(status_code: int) -> tuple[TicketToolAdapter, _AlwaysStatus]:
    client = _AlwaysStatus(status_code)
    return TicketToolAdapter(client_factory=lambda: client, base_url="http://java"), client


@pytest.mark.asyncio
async def test_a_retryable_status_is_actually_retried_for_a_read(monkeypatch):
    """`RETRYABLE_STATUS_CODES` was dead code: httpx does not raise, so nothing was classified."""
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "x" * 32, raising=False)
    monkeypatch.setattr(settings, "tool_retry_read_attempts", 3, raising=False)
    adapter, client = _ticket_adapter(503)

    result = await adapter.execute("ticket_list", {}, state())

    assert client.attempts == 3, "a 503 must be retried up to the read attempt limit"
    assert result["success"] is False
    assert result["error"] == "TICKET_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_retryable_status_is_not_retried_for_a_write(monkeypatch):
    """T1: once a write's request has landed its effect is unknown, so it is never repeated."""
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "x" * 32, raising=False)
    monkeypatch.setattr(settings, "tool_retry_write_attempts", 2, raising=False)
    adapter, client = _ticket_adapter(429)

    result = await adapter.execute("ticket_create", {"title": "打印机坏了"}, state())

    assert client.attempts == 1
    assert result["success"] is False
    assert result["error"] == "TICKET_SERVICE_ERROR"


# ------------------------------------------------------------------- fallback and budgets


@pytest.mark.asyncio
async def test_no_model_still_runs_the_rule_based_round(monkeypatch):
    """The log said "using rules"; the stop reason then prevented the rules from running."""
    monkeypatch.setattr(tools_node, "select_tools_for_request", lambda *a, **k: [])

    async def unavailable(_messages, _names, **_kwargs):
        raise FunctionCallingUnavailable("no model configured for tool calling")

    monkeypatch.setattr(tools_node, "model_turn", unavailable)

    _results, mode, rounds, accounting = await tools_node.run_tool_loop(state())

    assert mode == "rule-based"
    assert accounting["stopReason"] == "NO_MODEL"
    assert rounds == 1, "the rule-based round must still run when there is no model"


@pytest.mark.asyncio
async def test_a_budget_stop_does_not_run_rules_afterwards(monkeypatch):
    """The counterpart: rules must not spend the budget that just stopped the loop."""
    monkeypatch.setattr(settings, "agent_token_budget", 1, raising=False)
    monkeypatch.setattr(tools_node, "select_tools_for_request", lambda *a, **k: ["ticket_list"])
    monkeypatch.setattr(
        tools_node,
        "model_turn",
        scripted(ModelTurn(calls=[], assistant_message={"role": "assistant"},
                           usage={"promptTokens": 900, "completionTokens": 100})),
    )

    _results, _mode, rounds, accounting = await tools_node.run_tool_loop(state())

    assert accounting["stopReason"] == "TOKEN_BUDGET"
    assert rounds == 0


# ------------------------------------------------------------- the timeout budget itself


def test_the_node_idle_window_reads_settings(monkeypatch):
    """A constant here meant /ready validated a value the graph did not use."""
    monkeypatch.setattr(settings, "node_idle_timeout_seconds", 77.0, raising=False)

    assert root_module._tool_node_idle_timeout() == 77.0


def test_the_node_run_budget_accounts_for_retries(monkeypatch):
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 45.0, raising=False)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 30.0, raising=False)
    monkeypatch.setattr(settings, "tool_retry_read_attempts", 3, raising=False)
    monkeypatch.setattr(settings, "tool_retry_total_budget_seconds", 8.0, raising=False)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 60.0, raising=False)
    monkeypatch.setattr(settings, "agent_max_rounds", 3, raising=False)

    # 3 rounds x (60s model + min(45s deadline, 3x30s + 8s)) + 30s slack
    assert root_module._tool_node_run_timeout() == pytest.approx(345.0)


def test_the_lease_outlives_the_worst_single_call(monkeypatch):
    """A lease shorter than one call lets a replay take over a write that is still running."""
    monkeypatch.setattr(settings, "effect_lease_enabled", True, raising=False)
    monkeypatch.setattr(settings, "effect_lease_seconds", 0.0, raising=False)
    monkeypatch.setattr(settings, "tool_call_deadline_seconds", 45.0, raising=False)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 30.0, raising=False)
    monkeypatch.setattr(settings, "tool_retry_read_attempts", 3, raising=False)
    monkeypatch.setattr(settings, "tool_retry_total_budget_seconds", 8.0, raising=False)

    assert lease_seconds() >= 2 * settings.tool_call_deadline_seconds


def test_a_loop_stop_records_its_duration():
    """The duration used to be computed by the caller and then dropped on the floor."""
    tool_metrics.record_loop_stop("UNIT_TEST_STOP", rounds=2, duration_ms=1234.0)

    series = metrics.snapshot()[tool_metrics.METRIC_LOOP_DURATION]["series"]
    assert any("UNIT_TEST_STOP" in key for key in series), series


def test_readiness_reports_the_breaker_scope_and_the_call_deadline():
    """`/ready` must not let an operator read "threshold: 3" as a cross-replica guarantee."""
    from app.tools.resilience import describe_resilience

    described = describe_resilience()

    assert described["circuitBreaker"]["scope"] == "per-process"
    assert described["retry"]["callDeadlineSeconds"] == settings.tool_call_deadline_seconds


# ------------------------------------------------------- the timeout the user finally sees


class SilentGraph:
    """A graph that starts and then produces nothing at all."""

    async def astream(self, *_args, **_kwargs):
        await asyncio.sleep(3600)
        yield {}  # pragma: no cover - the generator is cancelled first


def _chat_request():
    from app.api.schemas import AgentStatePayload, ChatMessage, ChatRequest, UserContext

    user = UserContext(userId="u1")
    return ChatRequest(
        request_id="turn-timeout",
        user_context=user,
        conversation_id="c1",
        user_message_id="m1",
        assistant_message_id="m2",
        messages=[ChatMessage(role="user", content="打印机坏了")],
        latest_user_message="打印机坏了",
        agent_state=AgentStatePayload(requestId="turn-timeout", userContext=user),
    )


@pytest.mark.asyncio
async def test_a_stalled_turn_ends_with_a_timeout_code_and_a_chinese_message(monkeypatch):
    """The user-facing timeout used to be an English sentence with no code to switch on."""
    import json

    from app.api.routes import chat as chat_route

    monkeypatch.setattr(settings, "stream_idle_timeout_seconds", 0.05, raising=False)

    async def no_publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        chat_route.RedisEventStream, "publish", no_publish, raising=False
    )

    frames = [
        frame
        async for frame in chat_route._event_generator(
            _chat_request(), graph=SilentGraph()
        )
    ]
    payloads = [
        json.loads(frame.split("data: ", 1)[1].strip()) for frame in frames
    ]

    timeout = next(item for item in payloads if item.get("type") == "error")
    assert timeout["errorCode"] == "TURN_TIMEOUT"
    assert "没有进展" in timeout["message"]
    # The turn is still closed properly, so the client's reader ends rather than hanging.
    assert payloads[-1] == {"type": "complete"}


# ------------------------------------------------------------------- audit coverage


@pytest.mark.asyncio
async def test_a_guardrail_refusal_in_the_loop_is_audited(monkeypatch):
    """The approval path audited these; the conversational path only logged them."""
    recorded: list[dict] = []

    async def recorder(action, **kwargs):
        recorded.append({"action": action, **kwargs})

    monkeypatch.setattr(tools_node, "record_action", recorder)
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_list", {"bogus": "x"})))
    )

    results, _mode, _rounds, _accounting = await tools_node.run_tool_loop(state())

    assert results[0]["error"] == "GUARDRAIL_REFUSED"
    assert [entry["action"] for entry in recorded] == ["guardrail.tool_refused"]
    assert recorded[0]["outcome"] == "denied"
    assert recorded[0]["object_id"] == "ticket_list"
    assert recorded[0]["detail"]["turn_id"] == "turn-timeout"
