"""Function calling and the agent loop.

Two capabilities are pinned here.

**Function calling.** The model is offered the catalog's JSON Schemas and picks. An
approval-gated tool is never in the list, and a model's choice is a *request*: the agent
allow-list, the user's permission and the argument schema are applied afterwards.

**The loop.** The model sees each tool result and may decide it needs another call, so
"look up the ticket, then comment on it" is expressible. A loop introduces failure modes a single
shot did not have, and they are all tested here: an exact repeat must not execute twice (with
real writes that is a second ticket), rounds and calls are capped, and the mode a turn actually
used is reported rather than assumed.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.graph.nodes import tools as tools_node
from app.tools.calling import (
    FunctionCallingUnavailable,
    ModelTurn,
    ToolChoice,
    choose_tools,
    function_calling_enabled,
    note_routing_mode,
    routing_mode,
    routing_status,
)
from app.tools.catalog import direct_tools, openai_tools


def completion(*calls, arguments_as_json: bool = True) -> dict:
    tool_calls = []
    for index, (name, arguments) in enumerate(calls):
        tool_calls.append({
            "id": f"call_{index}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments) if arguments_as_json else arguments,
            },
        })
    return {"choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}]}


def turn(*calls) -> ModelTurn:
    """A ModelTurn as the node would receive it."""
    choices = [
        ToolChoice(name=name, arguments=arguments, call_id=f"call_{index}")
        for index, (name, arguments) in enumerate(calls)
    ]
    return ModelTurn(
        calls=choices,
        assistant_message={
            "role": "assistant",
            "tool_calls": [
                {"id": c.call_id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in choices
            ],
        },
    )


def scripted(*turns: ModelTurn, capture: list | None = None):
    """A fake `converse` that returns one turn per round and records the messages it saw."""
    queue = list(turns)

    async def fake(messages, _names, **_kwargs):
        if capture is not None:
            capture.append(list(messages))
        return queue.pop(0) if queue else ModelTurn(calls=[], assistant_message={})

    return fake


def state(**overrides):
    value = {
        "request_id": "req-1",
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


@pytest.fixture(autouse=True)
def _no_decision_streaming(monkeypatch):
    """Exercise the non-streaming seam by default.

    Decision streaming is a transport choice, and it has its own test with an injected stream.
    Turning it off here keeps the loop tests about the loop's behaviour rather than about which
    HTTP shape the model call took.
    """
    monkeypatch.setattr(settings, "agent_stream_decisions", False)
    yield


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch):
    """State-changing calls now go through the effect ledger, so keep it off the database.

    That indirection is the reason a replayed node cannot apply a write twice; in tests it must
    not reach for Postgres.
    """
    from app.core.effect_ledger import EffectLedger

    monkeypatch.setattr(tools_node, "effect_ledger", EffectLedger(postgres=None))
    yield


@pytest.fixture(autouse=True)
def _writes_are_not_suspended(monkeypatch):
    """Run the confirm tier as `direct` in this file.

    `ticket_create` is a `confirm`-tier write, so with the tier enabled the loop records a
    pending confirmation and executes nothing. That is the correct behaviour and it is covered
    end to end in ``test_write_confirmation.py``; leaving it on here would silently turn every
    write assertion below into an assertion about the confirmation path.
    """
    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)
    yield


@pytest.fixture
def adapter(monkeypatch):
    from app.tools.adapters.ticket import TicketToolAdapter

    calls: list[tuple[str, dict]] = []

    class Recording(TicketToolAdapter):
        async def execute(self, tool_name, args, state):
            calls.append((tool_name, args))
            return {"success": True, "text": f"{tool_name} 已执行", "data": {"number": 26}}

    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: Recording(base_url="http://j"))
    return calls


# --------------------------------------------------------------------------- payload


def test_the_payload_offers_only_direct_tools_with_the_catalog_schema():
    payload_tools = openai_tools(direct_tools())

    names = [entry["function"]["name"] for entry in payload_tools]
    assert "ticket_merge" not in names
    assert set(names) == set(direct_tools())
    for entry in payload_tools:
        parameters = entry["function"]["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False


@pytest.mark.asyncio
async def test_a_model_choice_is_parsed_into_a_call():
    async def poster(payload):
        assert payload["tool_choice"] == "auto"
        return completion(("ticket_create", {"title": "打印机坏了", "type": "incident"}))

    choices = await choose_tools("打印机坏了", ["ticket_create"], poster=poster)

    assert [c.name for c in choices] == ["ticket_create"]
    assert choices[0].arguments == {"title": "打印机坏了", "type": "incident"}


@pytest.mark.asyncio
async def test_arguments_may_arrive_as_a_mapping_or_as_json():
    async def mapping_poster(_payload):
        return completion(("ticket_list", {"limit": 5}), arguments_as_json=False)

    assert (await choose_tools("我的工单", ["ticket_list"], poster=mapping_poster))[0].arguments == {
        "limit": 5
    }


@pytest.mark.asyncio
async def test_malformed_entries_are_discarded_not_guessed():
    async def poster(_payload):
        return {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "ticket_list", "arguments": "{not json"}},
            {"function": {"name": "", "arguments": "{}"}},
            {"function": {"name": "ticket_create", "arguments": json.dumps({"title": "x"})}},
            "not a dict",
        ]}}]}

    choices = await choose_tools("x", ["ticket_create", "ticket_list"], poster=poster)
    assert [c.name for c in choices] == ["ticket_create"]


@pytest.mark.asyncio
async def test_no_tool_calls_means_no_tools():
    async def poster(_payload):
        return {"choices": [{"message": {"role": "assistant", "content": "你好"}}]}

    assert await choose_tools("你好", ["ticket_list"], poster=poster) == []


@pytest.mark.asyncio
async def test_without_a_model_it_refuses_rather_than_pretending(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    with pytest.raises(FunctionCallingUnavailable):
        await choose_tools("x", ["ticket_list"])


# --------------------------------------------------------------------------- routing report


def test_routing_mode_reflects_whether_a_model_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "function_calling_enabled", True)
    monkeypatch.setattr(settings, "deepseek_api_key", "key")
    assert function_calling_enabled() is True
    assert routing_mode() == "model"

    monkeypatch.setattr(settings, "deepseek_api_key", "")
    assert routing_mode() == "rule-based"


def test_the_report_distinguishes_configured_from_actually_used(monkeypatch):
    monkeypatch.setattr(settings, "function_calling_enabled", True)
    monkeypatch.setattr(settings, "deepseek_api_key", "key")

    note_routing_mode("rule-based")
    status = routing_status()
    assert status["configured"] == "model"
    assert status["lastObserved"] == "rule-based"
    assert status["fellBack"] is True
    # The loop's ceilings are part of the honest report: they bound cost and repeated effects.
    assert status["maxRounds"] == settings.agent_max_rounds
    assert status["maxToolCalls"] == settings.agent_max_tool_calls

    note_routing_mode("model")
    assert routing_status()["fellBack"] is False


# --------------------------------------------------------------------------- the loop


@pytest.mark.asyncio
async def test_one_round_runs_the_chosen_tool(monkeypatch, adapter):
    monkeypatch.setattr(
        tools_node, "converse", scripted(turn(("ticket_create", {"title": "打印机坏了"})))
    )
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    result = await tools_node.execute_tools(state(), {"configurable": {"emit": emit}})

    assert result["tool_routing"] == "model"
    assert result["tool_rounds"] == 1
    assert adapter[0][0] == "ticket_create"
    # The model's arguments are kept and the correlation values are added alongside them.
    assert adapter[0][1]["title"] == "打印机坏了"
    assert adapter[0][1]["latest_user_message"] == "我的打印机坏了"
    assert events[0]["chosenBy"] == "model"
    assert events[0]["round"] == 1


@pytest.mark.asyncio
async def test_a_second_round_can_depend_on_the_first_result(monkeypatch, adapter):
    seen: list[list] = []
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(
            turn(("ticket_list", {"limit": 5})),
            turn(("ticket_create", {"title": "新问题"})),
            capture=seen,
        ),
    )

    result = await tools_node.execute_tools(state())

    assert [name for name, _ in adapter] == ["ticket_list", "ticket_create"]
    assert result["tool_rounds"] == 2
    # This is the point of the loop: the second round saw the first round's result, fed back as a
    # `role: tool` message, so the model chose with knowledge it did not have before.
    second_round_messages = seen[1]
    tool_messages = [m for m in second_round_messages if m.get("role") == "tool"]
    assert tool_messages, second_round_messages
    assert json.loads(tool_messages[0]["content"])["success"] is True


@pytest.mark.asyncio
async def test_an_exact_repeat_is_refused_so_a_write_cannot_happen_twice(monkeypatch, adapter):
    # The stop-after-write rule would end the loop before the repeat; disabling it proves the
    # fingerprint guard is what actually protects the write.
    monkeypatch.setattr(settings, "agent_stop_after_write", False)
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(
            turn(("ticket_create", {"title": "打印机坏了"})),
            turn(("ticket_create", {"title": "打印机坏了"})),
        ),
    )

    result = await tools_node.execute_tools(state())

    # One execution, one refusal: with real writes the repeat would be a second ticket.
    assert [name for name, _ in adapter] == ["ticket_create"]
    refusals = [r for r in result["tool_results"] if not r["success"]]
    assert refusals and "重复调用已忽略" in refusals[0]["text"]


@pytest.mark.asyncio
async def test_a_successful_write_ends_the_loop(monkeypatch, adapter):
    # A write *is* the outcome; another round could only duplicate it or read what the answer can
    # already be written from.
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(
            turn(("ticket_create", {"title": "打印机坏了"})),
            turn(("ticket_list", {"limit": 5})),
        ),
    )

    result = await tools_node.execute_tools(state())

    assert [name for name, _ in adapter] == ["ticket_create"]
    assert result["tool_rounds"] == 1


@pytest.mark.asyncio
async def test_a_different_read_in_a_later_round_is_still_allowed(monkeypatch, adapter):
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(
            turn(("ticket_list", {"limit": 5})),
            turn(("ticket_list", {"limit": 20})),
        ),
    )

    await tools_node.execute_tools(state())

    # Suppression is by exact arguments, not by tool name, so a genuinely different call runs.
    assert [name for name, _ in adapter] == ["ticket_list", "ticket_list"]


@pytest.mark.asyncio
async def test_the_model_cannot_run_forever(monkeypatch, adapter):
    async def always(messages, _names, **_kwargs):  # noqa: ARG001
        # A different argument each time, so duplicate suppression never ends the loop.
        return turn(("ticket_list", {"limit": len(messages)}))

    monkeypatch.setattr(tools_node, "converse", always)

    result = await tools_node.execute_tools(state())

    assert result["tool_rounds"] == settings.agent_max_rounds
    assert len(adapter) == settings.agent_max_rounds


@pytest.mark.asyncio
async def test_the_total_number_of_calls_is_capped(monkeypatch, adapter):
    monkeypatch.setattr(settings, "agent_max_tool_calls", 2)

    async def many(messages, _names, **_kwargs):  # noqa: ARG001
        return turn(
            ("ticket_list", {"limit": len(messages)}),
            ("ticket_list", {"limit": len(messages) * 10}),
            ("ticket_list", {"limit": len(messages) * 100}),
        )

    monkeypatch.setattr(tools_node, "converse", many)
    await tools_node.execute_tools(state())

    assert len(adapter) == 2


@pytest.mark.asyncio
async def test_the_model_cannot_smuggle_in_arguments(monkeypatch, adapter):
    # `priority` is not in the catalog schema, so the guardrail refuses the whole call.
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(turn(("ticket_create", {"title": "x", "priority": "critical"}))),
    )

    result = await tools_node.execute_tools(state())

    assert adapter == []
    assert "护栏" in result["tool_results"][0]["text"]


@pytest.mark.asyncio
async def test_the_model_is_not_shown_tools_the_user_cannot_use(monkeypatch, adapter):
    seen: list[list[str]] = []

    async def fake(_messages, names, **_kwargs):
        seen.append(list(names))
        return ModelTurn(calls=[], assistant_message={})

    monkeypatch.setattr(tools_node, "converse", fake)

    # An auditor may read tickets but not create or comment on them.
    await tools_node.execute_tools(state(user_context={"roles": ["auditor"]}))

    assert seen == [["ticket_list", "ticket_get"]]


@pytest.mark.asyncio
async def test_an_agent_with_no_usable_tools_never_asks_the_model(monkeypatch, adapter):
    asked = []

    async def fake(_messages, names, **_kwargs):  # pragma: no cover - must not run
        asked.append(names)
        return ModelTurn(calls=[], assistant_message={})

    monkeypatch.setattr(tools_node, "converse", fake)

    result = await tools_node.execute_tools(state(selected_agent="guardian"))

    assert asked == []
    assert result["tool_routing"] == "rule-based"
    assert result["tool_rounds"] == 1


@pytest.mark.asyncio
async def test_a_model_failure_falls_back_to_the_rules_and_says_so(monkeypatch, adapter):
    async def broken(_messages, _names, **_kwargs):
        raise FunctionCallingUnavailable("no model")

    monkeypatch.setattr(tools_node, "converse", broken)

    result = await tools_node.execute_tools(state())

    assert result["tool_routing"] == "rule-based"
    # The rule-based path still raises the ticket: the service desk keeps working.
    assert adapter[0][0] == "ticket_create"


@pytest.mark.asyncio
async def test_an_approval_gated_tool_cannot_be_reached_through_the_loop(monkeypatch, adapter):
    monkeypatch.setattr(
        tools_node,
        "converse",
        scripted(turn(("ticket_merge", {"sourceNumber": 1, "targetNumber": 2}))),
    )

    result = await tools_node.execute_tools(state())

    assert adapter == []
    assert "需要审批" in result["tool_results"][0]["text"]


@pytest.mark.asyncio
async def test_no_tool_is_called_when_the_model_chooses_none(monkeypatch, adapter):
    monkeypatch.setattr(tools_node, "converse", scripted())

    result = await tools_node.execute_tools(state(latest_user_message="你是谁"))

    # An answer the assistant can give directly must not raise a ticket, and the turn is still a
    # model round -- so `/ready` will not report a fallback.
    assert adapter == []
    assert result["tool_routing"] == "model"
    assert result["tool_results"] == []
