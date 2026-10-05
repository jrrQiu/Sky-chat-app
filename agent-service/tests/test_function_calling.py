"""Function calling: the model chooses the tools.

The rules used to choose, by regex over the user's message. Now the model is offered the
catalog's JSON Schemas and picks -- with four things pinned here:

* an approval-gated tool is **never** in the offered list, so the model cannot be the reason a
  destructive action runs;
* a model's choice is a *request*: the agent allow-list, the user's permission and the argument
  schema are all applied afterwards;
* a model failure falls back to the rules and says so in `/ready`, rather than silently
  pretending the model chose;
* the tool payload the model sees matches the catalog exactly, so it cannot be handed a
  parameter the guardrail would then reject.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.graph.nodes import tools as tools_node
from app.tools.calling import (
    FunctionCallingUnavailable,
    choose_tools,
    function_calling_enabled,
    note_routing_mode,
    routing_mode,
    routing_status,
)
from app.tools.catalog import openai_tools, direct_tools


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
        # The request carries the schemas and asks the model to decide.
        assert payload["tool_choice"] == "auto"
        assert "tools" in payload
        return completion(("ticket_create", {"title": "打印机坏了", "type": "incident"}))

    choices = await choose_tools("打印机坏了", ["ticket_create"], poster=poster)

    assert len(choices) == 1
    assert choices[0].name == "ticket_create"
    assert choices[0].arguments == {"title": "打印机坏了", "type": "incident"}
    assert choices[0].call_id == "call_0"


@pytest.mark.asyncio
async def test_arguments_may_arrive_as_a_mapping_or_as_json():
    async def mapping_poster(_payload):
        return completion(("ticket_list", {"limit": 5}), arguments_as_json=False)

    choices = await choose_tools("我的工单", ["ticket_list"], poster=mapping_poster)
    assert choices[0].arguments == {"limit": 5}


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

    # Only the usable one survives; the rest are dropped rather than guessed at.
    assert [c.name for c in choices] == ["ticket_create"]


@pytest.mark.asyncio
async def test_no_tool_calls_means_no_tools():
    async def poster(_payload):
        return {"choices": [{"message": {"role": "assistant", "content": "你好"}}]}

    assert await choose_tools("你好", ["ticket_list"], poster=poster) == []


@pytest.mark.asyncio
async def test_an_empty_candidate_list_never_calls_the_model():
    async def poster(_payload):  # pragma: no cover - must not run
        raise AssertionError("no call should be made with no candidates")

    assert await choose_tools("x", [], poster=poster) == []


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

    # Configured for the model, but the last turn fell back: that difference is the point.
    assert status["configured"] == "model"
    assert status["lastObserved"] == "rule-based"
    assert status["fellBack"] is True

    note_routing_mode("model")
    assert routing_status()["fellBack"] is False


# --------------------------------------------------------------------------- the node


@pytest.fixture
def adapter(monkeypatch):
    from app.tools.adapters.ticket import TicketToolAdapter

    calls: list[tuple[str, dict]] = []

    class Recording(TicketToolAdapter):
        async def execute(self, tool_name, args, state):
            calls.append((tool_name, args))
            return {"success": True, "text": f"{tool_name} 已执行", "data": {"number": 7}}

    instance = Recording(base_url="http://java.test:8080")
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: instance)
    return calls


@pytest.mark.asyncio
async def test_the_model_choice_is_what_runs(monkeypatch, adapter):
    async def fake_choose(_message, _names, **_kwargs):
        from app.tools.calling import ToolChoice

        return [ToolChoice("ticket_create", {"title": "打印机坏了"}, "call_0")]

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    result = await tools_node.execute_tools(state(), {"configurable": {"emit": emit}})

    assert result["tool_routing"] == "model"
    assert adapter[0][0] == "ticket_create"
    # The model's arguments are kept, and the correlation values are added alongside them.
    assert adapter[0][1]["title"] == "打印机坏了"
    assert adapter[0][1]["latest_user_message"] == "我的打印机坏了"
    assert events[0]["chosenBy"] == "model"


@pytest.mark.asyncio
async def test_the_model_cannot_smuggle_in_arguments(monkeypatch, adapter):
    async def fake_choose(_message, _names, **_kwargs):
        from app.tools.calling import ToolChoice

        # `priority` is not in the catalog schema, so the guardrail must refuse the whole call.
        return [ToolChoice("ticket_create", {"title": "x", "priority": "critical"}, "call_0")]

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)

    result = await tools_node.execute_tools(state())

    assert adapter == []
    assert result["tool_results"][0]["success"] is False
    assert "护栏" in result["tool_results"][0]["text"]


@pytest.mark.asyncio
async def test_the_model_is_not_shown_tools_the_user_cannot_use(monkeypatch, adapter):
    seen: list[list[str]] = []

    async def fake_choose(_message, names, **_kwargs):
        seen.append(list(names))
        return []

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)

    # An auditor may read tickets but not create or comment on them.
    await tools_node.execute_tools(state(user_context={"roles": ["auditor"]}))

    assert seen == [["ticket_list"]]


@pytest.mark.asyncio
async def test_an_agent_with_no_usable_tools_never_asks_the_model(monkeypatch, adapter):
    asked = []

    async def fake_choose(_message, names, **_kwargs):  # pragma: no cover - must not run
        asked.append(names)
        return []

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)

    result = await tools_node.execute_tools(state(selected_agent="guardian"))

    assert asked == []
    # No candidates, so the rules are used and they select nothing either.
    assert result["tool_routing"] == "rule-based"


@pytest.mark.asyncio
async def test_a_model_failure_falls_back_to_the_rules_and_says_so(monkeypatch, adapter):
    async def broken(_message, _names, **_kwargs):
        raise FunctionCallingUnavailable("no model")

    monkeypatch.setattr(tools_node, "choose_tools", broken)

    result = await tools_node.execute_tools(state())

    assert result["tool_routing"] == "rule-based"
    # The rule-based path still raises the ticket: the service desk keeps working.
    assert adapter[0][0] == "ticket_create"


@pytest.mark.asyncio
async def test_an_approval_gated_tool_cannot_be_reached_through_this_node(monkeypatch, adapter):
    async def fake_choose(_message, _names, **_kwargs):
        from app.tools.calling import ToolChoice

        return [ToolChoice("ticket_merge", {"sourceNumber": 1, "targetNumber": 2}, "call_0")]

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)

    result = await tools_node.execute_tools(state())

    assert adapter == []
    assert result["tool_results"][0]["success"] is False
    assert "需要审批" in result["tool_results"][0]["text"]


@pytest.mark.asyncio
async def test_no_tool_is_called_when_the_model_chooses_none(monkeypatch, adapter):
    async def fake_choose(_message, _names, **_kwargs):
        return []

    monkeypatch.setattr(tools_node, "choose_tools", fake_choose)

    result = await tools_node.execute_tools(state(latest_user_message="你是谁"))

    # An answer the assistant can give directly must not raise a ticket.
    assert adapter == []
    assert result["tool_routing"] == "rule-based"
    assert result["tool_results"] == []
