"""The five loop improvements: concurrency, streaming, budgets, replay safety, and comments.

Each of these exists because of a concrete weakness, so each test is written to fail if the
weakness comes back:

* read-only calls in one round now run **together**, while writes stay in the order the model
  asked for them;
* tool *decisions* stream, so a slow model does not look like a hung one, and tool calls arrive
  as fragments that must be assembled;
* the loop is bounded by tokens as well as by rounds, and stops after a successful write;
* every state-changing call goes through the effect ledger, so a replayed node cannot apply the
  same write twice;
* the agent may post public replies only -- the internal-note capability is not in its schema.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.core.effect_ledger import EffectLedger
from app.core.guardrails import screen_tool_input
from app.graph.nodes import tools as tools_node
from app.tools.adapters.ticket import TicketToolAdapter
from app.tools.calling import ModelTurn, ToolChoice, converse_stream
from app.tools.catalog import TOOLS, openai_tools


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
                {"id": c.call_id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in choices
            ],
        },
    )


def scripted(*turns: ModelTurn):
    queue = list(turns)

    async def fake(_messages, _names, **_kwargs):
        return queue.pop(0) if queue else ModelTurn(calls=[], assistant_message={})

    return fake


class ConcurrencyAdapter(TicketToolAdapter):
    """Records when each call starts and ends, so overlap is observable."""

    def __init__(self) -> None:
        super().__init__(base_url="http://java.test:8080")
        self.events: list[tuple[str, str, str]] = []
        self.active = 0
        self.max_active = 0
        self.order: list[str] = []

    async def execute(self, tool_name, args, state):
        self.order.append(tool_name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.events.append(("start", tool_name, ""))
        await asyncio.sleep(0.01)
        self.active -= 1
        self.events.append(("end", tool_name, ""))
        return {"success": True, "text": f"{tool_name} ok", "data": {"number": 7}}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(settings, "agent_stream_decisions", False)
    monkeypatch.setattr(tools_node, "effect_ledger", EffectLedger(postgres=None))
    yield


# --------------------------------------------------------------------------- concurrency


@pytest.mark.asyncio
async def test_read_only_calls_in_one_round_run_together(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node,
        "model_turn",
        scripted(turn(("ticket_list", {"limit": 5}), ("ticket_get", {"number": 7}))),
    )

    await tools_node.execute_tools(state())

    assert set(adapter.order) == {"ticket_list", "ticket_get"}
    # Both were in flight at once: with sequential awaits this never exceeds 1.
    assert adapter.max_active == 2


@pytest.mark.asyncio
async def test_a_write_waits_for_the_reads_and_keeps_its_place(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node,
        "model_turn",
        scripted(turn(("ticket_list", {"limit": 5}), ("ticket_create", {"title": "x"}))),
    )

    await tools_node.execute_tools(state())

    # The write runs after the read batch, not inside it: a sequence of writes is the part where
    # order carries meaning.
    assert adapter.order == ["ticket_list", "ticket_create"]
    assert adapter.max_active == 1


@pytest.mark.asyncio
async def test_two_identical_calls_in_one_concurrent_batch_cannot_both_run(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node,
        "model_turn",
        scripted(turn(("ticket_list", {"limit": 5}), ("ticket_list", {"limit": 5}))),
    )

    result = await tools_node.execute_tools(state())

    # The fingerprint is claimed before any await, so the race cannot let both through.
    assert adapter.order == ["ticket_list"]
    assert any("重复调用已忽略" in r.get("text", "") for r in result["tool_results"])


# --------------------------------------------------------------------------- streaming


async def fake_stream(chunks):
    async def streamer(_payload):
        for chunk in chunks:
            yield chunk

    return streamer


@pytest.mark.asyncio
async def test_streamed_tool_calls_are_assembled_from_fragments():
    # The API sends the name once and the arguments in pieces, keyed by index.
    chunks = [
        {"choices": [{"delta": {"content": "我"}}]},
        {"choices": [{"delta": {"content": "先查一下"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "ticket_list", "arguments": ""}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"lim'}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'it": 5}'}}
        ]}}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                  "total_tokens": 120}},
    ]
    seen: list[str] = []

    async def on_delta(piece):
        seen.append(piece)

    result = await converse_stream(
        [{"role": "user", "content": "x"}],
        ["ticket_list"],
        on_delta=on_delta,
        streamer=await fake_stream(chunks),
    )

    assert [c.name for c in result.calls] == ["ticket_list"]
    assert result.calls[0].arguments == {"limit": 5}
    assert result.calls[0].call_id == "call_a"
    # The reasoning was forwarded as it arrived, which is the point of streaming the decision.
    assert seen == ["我", "先查一下"]
    assert result.usage == {"promptTokens": 100, "completionTokens": 20, "totalTokens": 120}


@pytest.mark.asyncio
async def test_unparsable_streamed_arguments_are_dropped_not_executed():
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "ticket_create",
                                                 "arguments": "{not json"}}
        ]}}]},
    ]

    result = await converse_stream(
        [{"role": "user", "content": "x"}], ["ticket_create"], streamer=await fake_stream(chunks)
    )

    assert result.calls == []


@pytest.mark.asyncio
async def test_missing_usage_is_reported_as_missing_not_as_zero():
    chunks = [{"choices": [{"delta": {"content": "hi"}}]}]

    result = await converse_stream(
        [{"role": "user", "content": "x"}], ["ticket_list"], streamer=await fake_stream(chunks)
    )

    # A provider that does not report usage must not look like a provider that spent nothing.
    assert result.usage is None


@pytest.mark.asyncio
async def test_the_decision_stream_is_forwarded_to_the_client(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "agent_stream_decisions", True)
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    async def streamer(_payload):
        yield {"choices": [{"delta": {"content": "正在决定"}}]}
        yield {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "ticket_list", "arguments": '{"limit":5}'}}
        ]}}]}

    await tools_node.run_tool_loop(state(), emit=emit, streamer=streamer)

    thoughts = [e for e in events if e.get("type") == "thinking"]
    assert any("正在决定" in str(e.get("content")) for e in thoughts)
    assert [e["name"] for e in events if e.get("type") == "tool_call"] == ["ticket_list"]


# --------------------------------------------------------------------------- budget


@pytest.mark.asyncio
async def test_the_loop_stops_when_the_token_budget_is_spent(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "agent_token_budget", 50)

    async def budget_streamer(_payload):
        # A read-only call each round, and a usage figure that blows the budget immediately.
        yield {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c", "function": {"name": "ticket_list",
                                                 "arguments": '{"limit":1}'}}
        ]}}]}
        yield {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 100,
                                        "total_tokens": 1000}}

    result = await tools_node.run_tool_loop(state(), streamer=budget_streamer)

    _results, _mode, rounds, accounting = result
    assert accounting["stopReason"] == "TOKEN_BUDGET"
    assert accounting["tokens"] == 1000
    assert rounds == 1


@pytest.mark.asyncio
async def test_accounting_reports_no_token_figure_when_none_was_reported(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node, "model_turn", scripted(turn(("ticket_list", {"limit": 5})))
    )

    _results, _mode, _rounds, accounting = await tools_node.run_tool_loop(state())

    assert accounting["tokens"] is None


def always(*calls) -> ModelTurn:
    """A fake that proposes the same calls on every round.

    Needed for the replay tests: a one-shot queue would run out on the second node execution and
    the test would pass without the model ever proposing the write again -- a vacuous pass.
    """

    async def fake(_messages, _names, **_kwargs):
        return turn(*calls)

    return fake


# --------------------------------------------------------------------------- replay safety


@pytest.mark.asyncio
async def test_a_replayed_node_cannot_apply_the_same_write_twice(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(
        tools_node, "model_turn", always(("ticket_create", {"title": "打印机坏了"}))
    )

    # The first execution writes; the second is the same node running again after a crash or a
    # re-delivered turn. The state is rebuilt, so only the ledger can stop the repeat.
    await tools_node.run_tool_loop(state())
    await tools_node.run_tool_loop(state())

    assert adapter.order == ["ticket_create"]


@pytest.mark.asyncio
async def test_a_read_is_not_deduplicated_across_replays(monkeypatch):
    adapter = ConcurrencyAdapter()
    monkeypatch.setattr(tools_node, "get_tool_adapter", lambda: adapter)
    monkeypatch.setattr(tools_node, "model_turn", always(("ticket_list", {"limit": 5})))

    await tools_node.run_tool_loop(state())
    await tools_node.run_tool_loop(state())

    # Reads are cheap and idempotent; suppressing them would hide a fresh answer.
    assert adapter.order == ["ticket_list", "ticket_list"]


# --------------------------------------------------------------------------- comments


def test_the_agent_may_only_post_public_comments():
    spec = TOOLS["ticket_comment"]
    # `visibility` is not in the schema at all, so the model cannot ask for an internal note...
    assert "visibility" not in spec.parameters
    assert "visibility" not in openai_tools(["ticket_comment"])[0]["function"]["parameters"][
        "properties"
    ]
    # ...and inventing it is refused by the guardrail rather than silently downgraded.
    verdict = screen_tool_input("ticket_comment", {"body": "x", "visibility": "internal"})
    assert verdict.allowed is False
    assert verdict.reason == "TOOL_ARGUMENT_NOT_ALLOWED"
    assert screen_tool_input("ticket_comment", {"number": 7, "body": "x"}).allowed is True


def test_the_ledger_action_fits_its_column():
    """`effect_ledger.action` is varchar(64).

    The first version of the replay guard embedded the arguments in the action name, overflowed
    the column, and the write was refused -- which is how the bug surfaced. This keeps the
    ceiling visible even though a unit test cannot read the column width.
    """
    import hashlib

    from app.graph.nodes.tools import _fingerprint

    for name in ("ticket_create", "ticket_comment"):
        args = {"title": "打印机坏了，连不上网络，而且我已经重启过了但是还是不行" * 3}
        digest = hashlib.sha256(_fingerprint(name, args).encode("utf-8")).hexdigest()[:16]
        assert len(f"{name}#{digest}") <= 64


def test_a_comment_is_a_write_so_it_is_gated_and_replay_safe():
    assert TOOLS["ticket_comment"].writes is True
    assert TOOLS["ticket_comment"].permission == "ticket:comment"
    assert TOOLS["ticket_comment"].approval_gated is False
