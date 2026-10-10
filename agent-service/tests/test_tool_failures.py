"""The single failure-classification table, and the two consumers that must agree with it.

The defect this pins: the metrics outcome and the circuit breaker each kept their own set of
error codes. ``INVALID_INTERNAL_CALLER`` was in neither, so a persistently rejected caller
identity was recorded as a generic failure **and** never counted towards the breaker -- the tool
loop kept calling a service that was answering 401 every time.
"""

from __future__ import annotations

import logging

import pytest

from app.config import settings
from app.graph.nodes import tools as tools_node
from app.observability import tool_metrics
from app.tools import failures
from app.tools.calling import ModelTurn, ToolChoice
from app.tools.resilience import BreakerRegistry

#: Every code the adapters and the loop can put on a tool result today.
#:
#: It is an explicit list on purpose: a new adapter that emits a new code should make someone
#: look here and decide, rather than have the code silently fall through as an unclassified
#: failure. ``TOOL_RETRY_BUDGET_EXHAUSTED`` is deliberately absent -- it is defined in
#: ``resilience`` but never put on a result.
EMITTED_CODES = (
    # adapters
    "TOOL_PROVIDER_UNAVAILABLE",
    "TOOL_PROVIDER_ERROR",
    "TOOL_NOT_IMPLEMENTED",
    "TICKET_SERVICE_UNAVAILABLE",
    "TICKET_SERVICE_ERROR",
    "INVALID_INTERNAL_CALLER",
    "CALLER_TOKEN_UNAVAILABLE",
    "MERGE_TARGETS_MISSING",
    "TOOL_REQUEST_REJECTED",
    "TOOL_TARGET_NOT_FOUND",
    # Java's business refusals: HTTP 200 with success=false and a TicketService code
    "TICKET_NOT_FOUND",
    "SPLIT_COMMENT_NOT_FOUND",
    "INVALID_TICKET_TYPE",
    "INVALID_TICKET_STATUS",
    "INVALID_LINK_TYPE",
    "INVALID_REQUEST",
    "TOOL_ARGUMENTS_MISSING",
    "MERGE_SELF",
    "LINK_SELF",
    "MERGE_CYCLE",
    "INVALID_TRANSITION",
    "LINK_DUPLICATE",
    "TICKET_ALREADY_MERGED",
    "FORBIDDEN_TICKET_ACTION",
    "INTERNAL_NOTE_FORBIDDEN",
    # the ledger and the loop
    "EFFECT_IN_PROGRESS",
    "RESULT_SCHEMA_VIOLATION",
    "TOOL_CIRCUIT_OPEN",
    "TOOL_CALL_TIMEOUT",
    "RESULT_UNKNOWN",
    "RBAC_DENIED",
    "AGENT_TOOL_NOT_ALLOWED",
    "APPROVAL_GATED",
    "DUPLICATE_CALL_REFUSED",
    "GUARDRAIL_REFUSED",
    "CONFIRMATION_REQUIRED",
    "CONFIRMATION_REJECTED",
    "TOOL_UNKNOWN",
)

_OUTCOMES = {
    failures.OUTCOME_OK,
    failures.OUTCOME_FAILED,
    failures.OUTCOME_REFUSED,
    failures.OUTCOME_DENIED,
    failures.OUTCOME_UNAVAILABLE,
}


def state(**overrides):
    value = {
        "request_id": "req-failures",
        "turn_id": "turn-failures",
        "user_id": "user-1",
        "user_context": {"roles": ["employee"]},
        "selected_agent": "it",
        "intent": "it.incident.report",
        "risk_level": "medium",
        "action": "query",
        "latest_user_message": "打印机坏了",
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


class Recording:
    def __init__(self, result=None) -> None:
        self.calls: list[str] = []
        self._result = result or {"success": True, "text": "ok", "data": {"number": 7}}

    async def execute(self, tool_name, args, state):  # noqa: ARG002 - adapter signature
        self.calls.append(tool_name)
        return dict(self._result)


# ------------------------------------------------------------------ the table itself


def test_every_emitted_code_is_classified():
    unknown = sorted(code for code in EMITTED_CODES if code not in failures.known_codes())
    assert unknown == [], f"unclassified tool error codes: {unknown}"


def test_every_code_the_java_service_returns_is_classified():
    """Read the Java source rather than trusting a hand-written list to stay in sync.

    The curated list above was written by hand and immediately drifted: a new Java code
    (``TOOL_ARGUMENTS_MISSING``) was added to the controller and not classified, which would
    have silently turned "you did not give me a ticket number" into a dead end instead of a
    question. The controller is the other half of this contract, so it is checked directly.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "java-service"
        / "src"
        / "main"
        / "java"
        / "com"
        / "skychat"
        / "controller"
        / "InternalToolController.java"
    )
    if not source.exists():
        pytest.skip("java-service is not checked out next to agent-service")

    text = source.read_text(encoding="utf-8")
    emitted = set(re.findall(r'failure\([^;]*?"([A-Z][A-Z0-9_]{3,})"', text, re.S))

    assert emitted, "the scan found no error codes at all, so it is no longer checking anything"
    unknown = sorted(code for code in emitted if code not in failures.known_codes())
    assert unknown == [], f"Java emits unclassified tool error codes: {unknown}"


@pytest.mark.parametrize("code", sorted(failures.known_codes()))
def test_every_known_code_has_a_coherent_class(code):
    cls = failures.classify(code)

    assert cls.outcome in _OUTCOMES
    assert cls.note, "a classification without an explanation is a classification nobody can audit"
    assert cls.recoverable in {
        failures.RECOVER_NONE,
        failures.RECOVER_ARGUMENTS,
        failures.RECOVER_PERMISSION,
    }


def test_a_missing_code_is_a_plain_failure():
    assert failures.classify(None).outcome == failures.OUTCOME_FAILED
    assert failures.classify("").outcome == failures.OUTCOME_FAILED


def test_an_unclassified_code_is_reported_rather_than_guessed(caplog):
    with caplog.at_level(logging.WARNING):
        cls = failures.classify("SOMETHING_NEW_FROM_AN_ADAPTER")

    assert cls.outcome == failures.OUTCOME_FAILED
    assert cls.transport is False
    assert "SOMETHING_NEW_FROM_AN_ADAPTER" in caplog.text


# --------------------------------------------------- the breaker's opinion of a code


def test_a_rejected_caller_identity_opens_the_breaker():
    """The regression: this code was in neither set, so the breaker never opened."""
    assert failures.counts_towards_breaker("INVALID_INTERNAL_CALLER") is True

    registry = BreakerRegistry(failure_threshold=3, cooldown_seconds=30.0)
    for _ in range(3):
        tools_node._record_breaker(
            registry, "ticket_list", {"success": False, "error": "INVALID_INTERNAL_CALLER"}
        )

    assert registry.breaker("ticket_list").is_open() is True


def test_our_own_refusal_does_not_open_the_breaker():
    """A refusal we produced says nothing about the dependency's health."""
    registry = BreakerRegistry(failure_threshold=3, cooldown_seconds=30.0)
    for _ in range(5):
        tools_node._record_breaker(
            registry, "ticket_list", {"success": False, "error": "RBAC_DENIED"}
        )

    assert registry.breaker("ticket_list").is_open() is False


def test_a_refusal_clears_a_stale_failure_count():
    registry = BreakerRegistry(failure_threshold=3, cooldown_seconds=30.0)
    tools_node._record_breaker(
        registry, "ticket_list", {"success": False, "error": "TOOL_PROVIDER_UNAVAILABLE"}
    )
    tools_node._record_breaker(
        registry, "ticket_list", {"success": False, "error": "RESULT_SCHEMA_VIOLATION"}
    )

    assert registry.breaker("ticket_list").failures == 0


# ---------------------------------------------- the two consumers agree through the loop


@pytest.mark.asyncio
async def test_the_agent_allow_list_refusal_carries_a_code_and_a_denied_outcome(monkeypatch):
    """The `knowledge` agent has the read-only ticket tools only, so a write must be refused.

    Every specialist agent that owns tickets currently lists all five ticket tools, so this
    gate can only be exercised through an agent whose catalog is narrower (or through a tool
    that does not exist yet).
    """
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: Recording())
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_create", {"title": "打印机坏了"})))
    )

    results, *_ = await tools_node.run_tool_loop(state(selected_agent="knowledge"))

    assert results[0]["error"] == "AGENT_TOOL_NOT_ALLOWED"
    assert tool_metrics.outcome_for(results[0]) == tool_metrics.OUTCOME_DENIED
    assert failures.counts_towards_breaker(results[0]["error"]) is False


@pytest.mark.asyncio
async def test_a_tool_outside_the_catalog_is_reported_as_unknown(monkeypatch):
    """A hallucinated tool name must not be described as "needs approval"."""
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: Recording())
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("reimage_the_laptop", {"x": 1})))
    )

    results, *_ = await tools_node.run_tool_loop(state())

    assert results[0]["error"] == "TOOL_UNKNOWN"
    assert "目录" in results[0]["text"]


@pytest.mark.asyncio
async def test_an_exact_repeat_carries_a_code_and_a_refused_outcome(monkeypatch):
    adapter = Recording()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node,
        "model_turn",
        scripted(turn(("ticket_list", {"limit": 5})), turn(("ticket_list", {"limit": 5}))),
    )

    results, *_ = await tools_node.run_tool_loop(state())

    assert adapter.calls == ["ticket_list"], "the repeat must not reach the adapter"
    assert results[-1]["error"] == "DUPLICATE_CALL_REFUSED"
    assert tool_metrics.outcome_for(results[-1]) == tool_metrics.OUTCOME_REFUSED


@pytest.mark.asyncio
async def test_a_success_still_maps_to_ok(monkeypatch):
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: Recording())
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_list", {"limit": 5})))
    )

    results, *_ = await tools_node.run_tool_loop(state())

    assert tool_metrics.outcome_for(results[0]) == tool_metrics.OUTCOME_OK


# ------------------------------------------------------------------- gate redundancy


def test_candidacy_is_already_permission_filtered():
    """The RBAC check inside ``guarded`` is defence in depth, not the first line.

    ``allowed_tools_for`` applies the same ``authorize_tool`` test, so a tool the caller may
    not use is never offered to the model in the first place. The inner check still matters --
    the model can name any tool, and a future candidate source may be laxer -- but it is worth
    knowing which of the two actually decides, because that is the one to test.
    """
    assert tools_node.allowed_tools_for(state(user_context={"roles": []})) == []
    assert "ticket_list" in tools_node.allowed_tools_for(state())


# ------------------------------------------- deterministic failures vs dependency failures


class _StatusClient:
    """An httpx-shaped client answering one status and one JSON body."""

    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.attempts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ARG002 - context manager protocol
        return False

    async def post(self, *args, **kwargs):  # noqa: ARG002 - client protocol
        self.attempts += 1
        return _Response(self.status_code, self.body)


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _adapter(status_code: int, body: dict | None = None):
    from app.tools.adapters.ticket import TicketToolAdapter

    client = _StatusClient(status_code, body)
    return TicketToolAdapter(client_factory=lambda: client, base_url="http://java"), client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,tool,expected,transport,recoverable",
    [
        # A 4xx is the dependency answering "your request is wrong": fixable, and never a
        # reason to open the breaker.
        (404, "ticket_list", "TOOL_TARGET_NOT_FOUND", False, failures.RECOVER_ARGUMENTS),
        (400, "ticket_list", "TOOL_REQUEST_REJECTED", False, failures.RECOVER_ARGUMENTS),
        (422, "ticket_list", "TOOL_REQUEST_REJECTED", False, failures.RECOVER_ARGUMENTS),
        (409, "ticket_list", "TOOL_REQUEST_REJECTED", False, failures.RECOVER_ARGUMENTS),
        # A retryable 5xx on a *read* is retried first, so it surfaces as "unreachable".
        (503, "ticket_list", "TICKET_SERVICE_UNAVAILABLE", True, failures.RECOVER_NONE),
        (500, "ticket_list", "TICKET_SERVICE_UNAVAILABLE", True, failures.RECOVER_NONE),
        # A write is never retried, so its 5xx is reported by the status branch.
        (503, "ticket_create", "TICKET_SERVICE_ERROR", True, failures.RECOVER_NONE),
        # A status outside the retryable set reaches the status branch even on a read.
        (501, "ticket_list", "TICKET_SERVICE_ERROR", True, failures.RECOVER_NONE),
    ],
)
async def test_the_status_code_decides_whether_we_or_they_are_at_fault(
    monkeypatch, status_code, tool, expected, transport, recoverable
):
    """A 4xx means the dependency answered and our request was wrong.

    Collapsing every status into one code had two costs: the caller could not tell a fixable
    request from an outage, and -- because that code counted as transport -- a caller bug could
    open the circuit breaker and refuse every subsequent call locally.
    """
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "x" * 32, raising=False)
    adapter, _client = _adapter(status_code)

    result = await adapter.execute(tool, {"title": "打印机坏了"}, state())

    assert result["error"] == expected
    cls = failures.classify(result["error"])
    assert cls.transport is transport
    assert cls.recoverable == recoverable


@pytest.mark.asyncio
async def test_the_dependencys_own_explanation_is_passed_through(monkeypatch):
    """`{"error": "..."}` from Java is what makes a rejection actionable for a user."""
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "x" * 32, raising=False)
    adapter, _client = _adapter(409, {"error": "该工单已关闭，不能合并"})

    result = await adapter.execute("ticket_list", {}, state())

    assert result["error"] == "TOOL_REQUEST_REJECTED"
    assert result["text"] == "该工单已关闭，不能合并"


def test_a_client_error_does_not_open_the_breaker():
    registry = BreakerRegistry(failure_threshold=3, cooldown_seconds=30.0)
    for _ in range(5):
        tools_node._record_breaker(
            registry, "ticket_get", {"success": False, "error": "TOOL_TARGET_NOT_FOUND"}
        )

    assert registry.breaker("ticket_get").is_open() is False


# ------------------------------------------------------------------- slot filling


def test_recovery_message_prefers_the_tools_own_words():
    assert (
        failures.recovery_message("MERGE_TARGETS_MISSING", "请提供要合并的两张工单编号")
        == "请提供要合并的两张工单编号"
    )
    assert failures.recovery_message("TOOL_TARGET_NOT_FOUND", "编号可能不存在") == "编号可能不存在"


def test_recovery_message_replaces_internal_wording_for_a_permission_failure():
    """`RBAC denied tool ... for current user roles` is written for us, not for the user."""
    message = failures.recovery_message("RBAC_DENIED", "RBAC denied tool ticket_create")

    assert message is not None
    assert "权限" in message
    assert "RBAC" not in message


def test_a_dependency_failure_asks_the_user_nothing():
    assert failures.recovery_message("TICKET_SERVICE_UNAVAILABLE", "服务暂时不可用") is None


def test_clarification_only_when_nothing_succeeded_and_the_user_can_fix_it():
    fixed = {"toolName": "ticket_get", "success": False, "error": "TOOL_TARGET_NOT_FOUND",
             "text": "编号可能不存在"}
    outage = {"toolName": "ticket_list", "success": False,
              "error": "TICKET_SERVICE_UNAVAILABLE", "text": "服务暂时不可用"}
    ok = {"toolName": "ticket_list", "success": True, "text": "ok"}

    question, fields = tools_node._clarification_for([fixed])
    assert question == "编号可能不存在"
    assert fields == ["ticket_get"]

    assert tools_node._clarification_for([outage]) == (None, [])
    assert tools_node._clarification_for([fixed, ok]) == (None, [])
    assert tools_node._clarification_for([]) == (None, [])


@pytest.mark.asyncio
async def test_a_missing_ticket_ends_the_turn_with_a_question_instead_of_a_dead_end(monkeypatch):
    monkeypatch.setattr(
        tools_node,
        "get_tool_adapter",
        lambda: Recording(
            {
                "success": False,
                "text": "工单服务中没有找到目标对象（HTTP 404），编号可能不存在。",
                "error": "TOOL_TARGET_NOT_FOUND",
            }
        ),
    )
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_get", {"number": 999})))
    )

    delta = await tools_node.execute_tools(state(), {"configurable": {"emit": None}})

    assert delta["clarification_required"] is True
    assert "编号" in delta["clarifying_question"]
    assert delta["missing_fields"] == ["ticket_get"]


@pytest.mark.asyncio
async def test_a_java_business_refusal_on_http_200_still_asks_the_user(monkeypatch):
    """Java refuses a tool with 200 + success=false + a code, which is its *normal* shape.

    This is the path the flagship case takes ("look up ticket #999"), so an entry in the
    classification table is what decides whether the user gets asked for a better number or
    just reads a dead end.
    """
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "x" * 32, raising=False)
    adapter, _client = _adapter(
        200,
        {"success": False, "text": "工单不存在或无权查看", "error": "TICKET_NOT_FOUND"},
    )

    result = await adapter.execute("ticket_get", {"number": 999}, state())

    assert result["success"] is False
    assert result["error"] == "TICKET_NOT_FOUND"
    assert failures.counts_towards_breaker(result["error"]) is False
    assert failures.recovery_message(result["error"], result["text"]) == "工单不存在或无权查看"


def test_a_state_fact_does_not_produce_a_question():
    """`INVALID_TRANSITION` ("already in that state") is not fixable by answering."""
    assert failures.recovery_message("INVALID_TRANSITION", "工单已处于该状态") is None


def test_an_assignee_only_action_asks_for_authorisation():
    message = failures.recovery_message("FORBIDDEN_TICKET_ACTION", "只有处理人可以变更工单状态")

    assert message is not None
    assert "权限" in message


@pytest.mark.asyncio
async def test_the_loop_asks_the_user_after_a_java_business_refusal(monkeypatch):
    monkeypatch.setattr(
        tools_node,
        "get_tool_adapter",
        lambda: Recording(
            {"success": False, "text": "工单不存在或无权查看", "error": "TICKET_NOT_FOUND"}
        ),
    )
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_get", {"number": 999})))
    )

    delta = await tools_node.execute_tools(state(), {"configurable": {"emit": None}})

    assert delta["clarification_required"] is True
    assert delta["clarifying_question"] == "工单不存在或无权查看"


@pytest.mark.asyncio
async def test_the_question_is_answered_without_calling_the_model(monkeypatch):
    from app.graph.nodes import generate as generate_node

    async def forbidden(**_kwargs):  # pragma: no cover - must never run
        raise AssertionError("asking the user must not cost a model call")
        yield  # pragma: no cover

    monkeypatch.setattr(generate_node, "stream_completion", forbidden)
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    result = await generate_node.generate_answer(
        {
            **state(),
            "clarification_required": True,
            "clarifying_question": "请提供工单编号后重试。",
            "missing_fields": ["ticket_get"],
        },
        {"configurable": {"emit": emit}},
    )

    assert result["final_answer"] == "请提供工单编号后重试。"
    kinds = [event["type"] for event in events]
    assert kinds == ["thinking", "clarification_required", "answer"]
    structured = next(event for event in events if event["type"] == "clarification_required")
    assert structured["missingFields"] == ["ticket_get"]


# --------------------------------------------------- the model-facing failure contract
#
# `classify` answers "can asking the user help?". The model needs a different question
# answered -- "what do I do next?" -- and it cannot work that out itself: it has no way to
# know whether a write landed or whether a call is safe to repeat. These tests pin the
# contract that replaces inference with instruction.


def test_every_known_code_has_model_facing_advice():
    """An un-advised code reaches the model as a generic "do not retry" default.

    That default is a *lie* for a code that is safe to repeat, so a gap here is a
    correctness bug rather than a missing nicety.
    """
    assert failures.codes_without_advice() == frozenset()


@pytest.mark.parametrize("code", sorted(failures.known_codes()))
def test_every_advice_is_coherent(code):
    advice = failures.advice_for(code)

    assert advice.reason in _OUTCOMES
    assert advice.side_effect in {failures.SIDE_EFFECT_NONE, failures.SIDE_EFFECT_UNKNOWN}
    assert advice.recovery in {
        failures.RECOVERY_NONE,
        failures.RECOVERY_RETRY,
        failures.RECOVERY_CHANGE_TOOL,
        failures.RECOVERY_CHANGE_ARGUMENTS,
        failures.RECOVERY_ASK_USER,
    }
    assert advice.guidance, "an instruction the model cannot read is not an instruction"
    assert isinstance(advice.retryable, bool)


@pytest.mark.parametrize(
    "code,retryable,side_effect,recovery",
    [
        # The request never landed: replay cannot duplicate, so it is safe to repeat.
        ("TOOL_PROVIDER_UNAVAILABLE", True, failures.SIDE_EFFECT_NONE, failures.RECOVERY_RETRY),
        ("TICKET_SERVICE_UNAVAILABLE", True, failures.SIDE_EFFECT_NONE, failures.RECOVERY_RETRY),
        # The effect may have landed: never claim success, never silently repeat.
        ("RESULT_UNKNOWN", False, failures.SIDE_EFFECT_UNKNOWN, failures.RECOVERY_NONE),
        ("TOOL_CALL_TIMEOUT", False, failures.SIDE_EFFECT_UNKNOWN, failures.RECOVERY_NONE),
        ("RESULT_SCHEMA_VIOLATION", False, failures.SIDE_EFFECT_UNKNOWN, failures.RECOVERY_NONE),
        ("TOOL_PROVIDER_ERROR", False, failures.SIDE_EFFECT_UNKNOWN, failures.RECOVERY_NONE),
        # Refused before anything was attempted: nothing happened, and no retry helps.
        ("EFFECT_IN_PROGRESS", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_NONE),
        ("TOOL_CIRCUIT_OPEN", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_NONE),
        # The action is wrong for this user: neither retrying nor re-arguing fixes it.
        ("RBAC_DENIED", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_ASK_USER),
        # The *tool* is unusable here: the model must pick a different one.
        ("TOOL_UNKNOWN", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_CHANGE_TOOL),
        ("AGENT_TOOL_NOT_ALLOWED", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_CHANGE_TOOL),
        ("APPROVAL_GATED", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_CHANGE_TOOL),
        ("TOOL_NOT_IMPLEMENTED", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_CHANGE_TOOL),
        # The *arguments* are wrong: a different value can work.
        ("TICKET_NOT_FOUND", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_CHANGE_ARGUMENTS),
        ("INVALID_TICKET_TYPE", False, failures.SIDE_EFFECT_NONE,
         failures.RECOVERY_CHANGE_ARGUMENTS),
        # A state fact: explaining is the whole response.
        ("INVALID_TRANSITION", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_NONE),
        # The user declined -- the signal is "wrong action", not "wrong argument".
        ("CONFIRMATION_REJECTED", False, failures.SIDE_EFFECT_NONE, failures.RECOVERY_ASK_USER),
    ],
)
def test_the_code_alone_decides_retry_and_side_effect(
    code, retryable, side_effect, recovery
):
    """These values must not be derivable from prose, so they are asserted per code."""
    advice = failures.advice_for(code)

    assert advice.retryable is retryable
    assert advice.side_effect == side_effect
    assert advice.recovery == recovery


def test_a_write_that_never_landed_is_reported_as_safe_to_repeat():
    """Write safety is the one case the model genuinely cannot reason about."""
    advice = failures.advice_for("TICKET_SERVICE_UNAVAILABLE")

    assert advice.retryable is True
    assert advice.side_effect == failures.SIDE_EFFECT_NONE, (
        "a connection that was never established cannot have applied anything"
    )


def test_an_unclassified_code_gets_the_conservative_default(caplog):
    with caplog.at_level(logging.WARNING):
        advice = failures.advice_for("SOMETHING_NOBODY_CLASSIFIED")

    assert advice.retryable is False
    assert advice.side_effect == failures.SIDE_EFFECT_UNKNOWN
    assert advice.recovery == failures.RECOVERY_NONE


def test_advice_serialises_with_camel_case_for_the_model():
    payload = failures.advice_for("TOOL_UNKNOWN").to_dict()

    assert set(payload) == {"reason", "retryable", "sideEffect", "recovery", "guidance"}


def test_the_tool_result_message_carries_the_advice():
    """The contract only works if it actually reaches the model."""
    from app.tools.calling import tool_result_message

    message = tool_result_message(
        "call_1",
        {
            "toolName": "ticket_create",
            "success": False,
            "text": "用户未确认该写操作，未执行。",
            "error": "CONFIRMATION_REJECTED",
        },
        tool_name="ticket_create",
    )
    import json

    payload = json.loads(message["content"])

    assert payload["success"] is False
    assert payload["error"] == "CONFIRMATION_REJECTED"
    assert payload["advice"]["recovery"] == failures.RECOVERY_ASK_USER
    assert payload["advice"]["retryable"] is False
    assert payload["advice"]["sideEffect"] == failures.SIDE_EFFECT_NONE


def test_a_successful_result_carries_no_advice():
    from app.tools.calling import tool_result_message

    message = tool_result_message(
        "call_1",
        {"toolName": "ticket_list", "success": True, "text": "ok"},
        tool_name="ticket_list",
    )
    import json

    assert "advice" not in json.loads(message["content"])


def test_the_answer_prompt_keeps_the_error_code_and_recovery():
    """Otherwise the answering model sees only "失败" and cannot tell
    "nothing happened" from "the effect may have landed"."""
    from app.context.budget import _render_tool_result

    rendered = _render_tool_result(
        {
            "toolName": "ticket_create",
            "success": False,
            "text": "用户未确认该写操作，未执行。",
            "error": "CONFIRMATION_REJECTED",
            "advice": {"recovery": "ask_user"},
        }
    )

    assert "CONFIRMATION_REJECTED" in rendered
    assert "ask_user" in rendered


# ------------------------------------------- self-correction is enforced, not hoped for


@pytest.mark.asyncio
async def test_a_refused_tool_is_withdrawn_and_the_model_is_told_why(monkeypatch):
    """After a `change_tool` refusal the model must be told to pick something else.

    Note where the refusal actually comes from: `allowed_tools_for` already applies the
    catalog, agent and RBAC filters, so a tool that is *not usable here* is normally
    never offered in the first place. The reachable path is the model naming a tool
    that was not offered -- a hallucinated name, or one from another agent's catalog --
    and the loop must answer that with an instruction, not silence.
    """
    adapter = Recording()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)

    seen_messages: list[list[dict]] = []

    async def fake_turn(_messages, _names, **_kwargs):
        seen_messages.append(list(_messages))
        if len(seen_messages) == 1:
            return turn(("reimage_the_laptop", {"x": 1}))
        return ModelTurn(calls=[], assistant_message={"role": "assistant"})

    monkeypatch.setattr(tools_node, "model_turn", fake_turn)

    results, *_ = await tools_node.run_tool_loop(state())

    assert results[0]["error"] == "TOOL_UNKNOWN"
    assert failures.advice_for("TOOL_UNKNOWN").recovery == failures.RECOVERY_CHANGE_TOOL
    assert adapter.calls == [], "a tool outside the catalog never reaches the adapter"

    follow_up = seen_messages[-1]
    guidance = [
        m for m in follow_up
        if m.get("role") == "system" and "reimage_the_laptop" in str(m.get("content", ""))
    ]
    assert guidance, "the model must be told the tool is unavailable, not left to guess"
    assert "不要重复调用" in guidance[-1]["content"]


@pytest.mark.asyncio
async def test_the_tool_result_itself_tells_the_model_not_to_repeat(monkeypatch):
    """Even without the system hint, the advice travels on the result the model reads."""
    adapter = Recording()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)

    payloads: list[str] = []
    real = tools_node.tool_result_message

    def spy(call_id, result, **kwargs):
        message = real(call_id, result, **kwargs)
        payloads.append(str(message.get("content", "")))
        return message

    monkeypatch.setattr(tools_node, "tool_result_message", spy)
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("reimage_the_laptop", {"x": 1})))
    )

    await tools_node.run_tool_loop(state())

    assert payloads, "the model must have received a tool result"
    assert "change_tool" in payloads[0]
    assert "retryable" in payloads[0]


