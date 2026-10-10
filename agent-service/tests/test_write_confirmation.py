"""The `confirm` tier's graph node (T7, PR6).

The interaction contract, and the reason it is tested through a **real compiled graph**:
`interrupt()` only works inside a runnable context, so a bare call to the node would prove
nothing about whether the pause can actually be resumed. Driving ``graph.astream`` with a
``Command(resume=...)`` is the same path the HTTP resume endpoint uses, so what is asserted
here is what production does.

Three properties make the design safe, and each is asserted:

1. the interrupt happens *before* any side effect, and the pending calls come from state (ids
   stable across the replay) rather than from a fresh model decision;
2. a rejection leaves no trace -- no adapter call, no ledger row;
3. an approval still obeys RBAC, the result contract and the effect ledger. Confirmation
   authorises the action; it does not grant a permission or bypass a contract.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from app.core.effect_ledger import effect_ledger
from app.graph.nodes import tools as tools_node
from app.graph.state import AgentState


def pending_call() -> dict:
    return {
        "callId": "call_0",
        "name": "ticket_create",
        "args": {
            "request_id": "req-1",
            "user_id": "user-1",
            "title": "打印机坏了",
            "latest_user_message": "我的打印机坏了",
        },
        "round": 1,
    }


def state_with_pending(**overrides) -> dict:
    value = {
        "request_id": "req-1",
        "turn_id": "turn-confirm",
        "user_id": "user-1",
        "user_context": {"roles": ["employee"]},
        "selected_agent": "it",
        "intent": "it.incident.report",
        "action": "submit",
        "latest_user_message": "我的打印机坏了",
        "pending_confirm_writes": [pending_call()],
        "retrieved_documents": [],
    }
    value.update(overrides)
    return value


class RecordingAdapter:
    def __init__(self, result=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {
            "success": True,
            "text": "已创建工单 #7",
            "data": {"number": 7},
        }

    async def execute(self, tool_name, args, state):
        self.calls.append((tool_name, args))
        return dict(self.result)


def build_confirm_graph():
    """`confirm_writes` between a seed node and a sink, with a real checkpointer."""
    graph = StateGraph(AgentState)
    graph.add_node("confirm_writes", tools_node.confirm_writes)
    graph.add_node("after", lambda state: {})
    graph.set_entry_point("confirm_writes")
    graph.add_edge("confirm_writes", "after")
    graph.add_edge("after", END)
    return graph.compile(checkpointer=InMemorySaver())


def run_graph(graph, state: dict, thread: str):
    """Run to completion or to the first interrupt, mirroring `run_graph` in the runner."""

    async def scenario():
        interrupts: list[dict] = []
        async for chunk in graph.astream(
            state, config={"configurable": {"thread_id": thread}}, stream_mode="updates"
        ):
            if "__interrupt__" in chunk:
                interrupts.append(chunk["__interrupt__"][0].value)
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread}})
        return interrupts, snapshot

    return asyncio.run(scenario())


def resume_graph(graph, decision: dict, thread: str):
    async def scenario():
        async for _chunk in graph.astream(
            Command(resume=decision),
            config={"configurable": {"thread_id": thread}},
            stream_mode="updates",
        ):
            pass
        return await graph.aget_state({"configurable": {"thread_id": thread}})

    return asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _isolate():
    from app.tools.resilience import reset_breakers

    original = tools_node.get_tool_adapter
    effect_ledger.reset()
    reset_breakers()
    yield
    tools_node.get_tool_adapter = original
    effect_ledger.reset()
    reset_breakers()


def test_no_pending_writes_is_a_pass_through():
    """The common case: the node must cost nothing when nothing needs confirming."""
    assert asyncio.run(tools_node.confirm_writes(state_with_pending(pending_confirm_writes=[]))) == {}


def test_the_graph_pauses_and_the_payload_is_answerable_by_the_requester(monkeypatch):
    """The pause reuses the approval record, so the payload has to be decidable.

    `required_approver_roles = "requester"` is the explicit value that means "the person who
    asked may answer this one". Blank would also pass the role test, but blank is
    indistinguishable from "nobody thought about it", and the Java side refuses a
    self-decision unless the sentinel is present.
    """
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()

    interrupts, snapshot = run_graph(graph, state_with_pending(), "thread-pause")

    assert len(interrupts) == 1, "the graph did not pause"
    payload = interrupts[0]
    assert payload["kind"] == "write_confirmation"
    assert payload["rule_id"] == tools_node.CONFIRMATION_RULE_ID
    assert payload["required_approver_roles"] == "requester"
    assert payload["turn_id"] == "turn-confirm"
    assert payload["user_id"] == "user-1"
    assert [item["name"] for item in payload["writes"]] == ["ticket_create"]
    assert "ticket_create" in payload["requested_action"]

    # Nothing has happened yet: the pause is before the write, not after it.
    assert adapter.calls == []
    assert snapshot.next, "the run must still be resumable"


def test_approving_executes_the_write_once(monkeypatch):
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()
    thread = "thread-approve"

    run_graph(graph, state_with_pending(), thread)
    snapshot = resume_graph(graph, {"decision": "approve"}, thread)

    assert [name for name, _args in adapter.calls] == ["ticket_create"]
    results = snapshot.values.get("tool_results") or []
    assert results and results[0]["success"] is True
    assert results[0]["data"]["number"] == 7
    assert snapshot.values.get("pending_confirm_writes") == []
    assert snapshot.values.get("write_confirmations") == [
        {"callId": "call_0", "decision": "approved"}
    ]


def test_rejecting_produces_no_call(monkeypatch):
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()
    thread = "thread-reject"

    run_graph(graph, state_with_pending(), thread)
    snapshot = resume_graph(graph, {"decision": "reject"}, thread)

    assert adapter.calls == [], "a rejected write must not reach the adapter"
    results = snapshot.values.get("tool_results") or []
    assert results and results[0]["error"] == "CONFIRMATION_REJECTED"
    assert snapshot.values.get("write_confirmations") == [
        {"callId": "call_0", "decision": "rejected"}
    ]


def test_an_approved_write_still_obeys_rbac(monkeypatch):
    """Confirmation authorises the action; it does not grant a permission."""
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()
    thread = "thread-rbac"

    run_graph(graph, state_with_pending(user_context={"roles": ["auditor"]}), thread)
    snapshot = resume_graph(graph, {"decision": "approve"}, thread)

    assert adapter.calls == []
    results = snapshot.values.get("tool_results") or []
    assert results[0]["error"] == "RBAC_DENIED"


def test_an_approved_write_still_obeys_the_result_contract(monkeypatch):
    adapter = RecordingAdapter(result={"success": True, "text": "done", "data": {}})
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()
    thread = "thread-contract"

    run_graph(graph, state_with_pending(), thread)
    snapshot = resume_graph(graph, {"decision": "approve"}, thread)

    results = snapshot.values.get("tool_results") or []
    assert results[0]["success"] is False
    assert results[0]["error"] == tools_node.RESULT_SCHEMA_VIOLATION


def test_an_approved_write_goes_through_the_ledger(monkeypatch):
    """The confirmed write is a write: a replay must not apply it twice."""
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)

    state = state_with_pending(
        write_confirmations=[{"callId": "call_0", "decision": "approved"}]
    )
    first = asyncio.run(tools_node.confirm_writes(state))
    second = asyncio.run(tools_node.confirm_writes(state))

    assert len(adapter.calls) == 1, "the second pass re-applied the write"
    assert first["tool_results"][0]["data"]["number"] == 7
    assert second["tool_results"][0]["deduplicated"] is True


def test_confirmation_disabled_executes_immediately(monkeypatch):
    """The rollout switch: with confirmation off the tier behaves like `direct`, and `/ready`
    reports that, so "the agent can create tickets silently" is never an accident."""
    from app.config import settings

    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "write_confirmation_enabled", False, raising=False)

    graph = build_confirm_graph()
    interrupts, snapshot = run_graph(graph, state_with_pending(), "thread-off")

    assert interrupts == [], "confirmation was disabled but the graph still paused"
    assert [name for name, _args in adapter.calls] == ["ticket_create"]


def test_a_second_resume_does_not_re_execute(monkeypatch):
    """Resuming twice is the shape a retrying client produces; it must be idempotent."""
    adapter = RecordingAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    graph = build_confirm_graph()
    thread = "thread-twice"

    run_graph(graph, state_with_pending(), thread)
    resume_graph(graph, {"decision": "approve"}, thread)
    resume_graph(graph, {"decision": "approve"}, thread)

    assert len(adapter.calls) == 1
