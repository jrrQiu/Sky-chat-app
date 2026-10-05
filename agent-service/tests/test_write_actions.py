"""Approved high-risk execution: the approval is real, so the execution must not lie.

Two things are being pinned here, and they are different in kind.

**What production declares today.** Exactly one action, `ticket_merge`: real, implemented
against the Java ticket service, irreversible (the source ticket becomes a tombstone), and
authorized as the *requester* -- `TicketService.merge` requires a staff caller, so the merge is
dual control rather than the system acting for someone who could not do it themselves.

**Capabilities no production action uses yet.** The `authority=system` model, and the
`external_implemented=False` + `records_change_ticket=True` pair that keeps an approved request
in our own system when the target system is not integrated. The fictional actions that used to
occupy those slots were deleted: connecting them would have meant writing an HTTP client against
an API that does not exist. The capabilities stay tested -- with an action declared for the test
-- so that declaring a real one later is a data change, not a rewrite.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

import pytest

from app.config import settings
from app.core.effect_ledger import EffectLedger
from app.core.guardrails import screen_tool_input
from app.graph.nodes import service_workflow as sw
from app.tools import actions as actions_module
from app.tools.actions import (
    ACTIONS,
    APPROVAL_ACTION_MISMATCH,
    APPROVAL_ALREADY_CONSUMED,
    APPROVAL_EXPIRED,
    APPROVAL_MISSING,
    APPROVAL_NOT_APPROVED,
    APPROVAL_NOT_AUTHORIZED,
    APPROVAL_NOT_FOUND,
    APPROVAL_REQUESTER_MISMATCH,
    APPROVAL_SELF_DECIDED,
    AUTHORITY_REQUESTER,
    AUTHORITY_SYSTEM,
    NOT_IMPLEMENTED,
    WriteAction,
    action_key,
    action_status,
    describe_outcome,
    evaluate_system_authority,
    refusal_message,
    resolve,
)
from app.tools.adapters.base import RefusingToolAdapter

#: A system-executed action declared only for the tests, standing in for a real third-party
#: integration until one exists.
SYSTEM_ACTION = "test_system_write"
#: A declared-but-not-integrated action, for the "approved, not executed" behaviour.
PENDING_ACTION = "test_pending_write"


@pytest.fixture(autouse=True)
def synthetic_actions(monkeypatch):
    monkeypatch.setitem(
        actions_module.ACTIONS,
        SYSTEM_ACTION,
        WriteAction(
            name=SYSTEM_ACTION,
            description="测试用系统执行动作",
            external_implemented=False,
            records_change_ticket=True,
            authority=AUTHORITY_SYSTEM,
        ),
    )
    monkeypatch.setitem(
        actions_module.ACTIONS,
        PENDING_ACTION,
        WriteAction(
            name=PENDING_ACTION,
            description="测试用未接入动作",
            external_implemented=False,
            records_change_ticket=True,
            authority=AUTHORITY_REQUESTER,
        ),
    )
    yield


class RecordingAdapter:
    """Stands in for the tool layer; records what it was asked to run."""

    def __init__(self, ticket_number: int | None = 4321) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ticket_number = ticket_number
        # Set to simulate a target system that exists.
        self.merge_result: dict[str, Any] = {
            "success": True,
            "text": "已把工单 #12 合并到 #14",
            "data": {"source": {"number": 12}, "target": {"number": 14}},
        }
        self.merge_error: Exception | None = None

    async def execute(self, tool_name, args, state):
        self.calls.append((tool_name, args))
        if tool_name == "ticket_create":
            return {
                "success": True,
                "text": f"已创建工单 #{self._ticket_number}",
                "data": {"number": self._ticket_number, "type": args.get("type")},
            }
        if tool_name == "ticket_merge":
            if self.merge_error is not None:
                raise self.merge_error
            return self.merge_result
        # No mock provider exists any more: anything unimplemented refuses.
        return await RefusingToolAdapter().execute(tool_name, args, state)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def wiring(monkeypatch):
    """Isolated ledger, recorded audit, and a recording adapter."""
    monkeypatch.setattr(sw, "effect_ledger", EffectLedger(postgres=None))

    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        sw,
        "record_action",
        lambda action, **kwargs: _record(recorded, action, kwargs),
    )

    adapter = RecordingAdapter()
    monkeypatch.setattr(sw, "get_tool_adapter", lambda: adapter)
    return {"adapter": adapter, "audit": recorded}


async def _record(sink: list[dict[str, Any]], action: str, kwargs: dict[str, Any]) -> None:
    sink.append({"action": action, **kwargs})


def base_state(**overrides):
    """An approved write by a requester who holds the write permission (dual control)."""
    state = {
        "request_id": "req-1",
        "turn_id": "turn-1",
        "user_id": "user-1",
        "user_context": {"roles": ["it_staff", "employee"]},
        "selected_agent": "it",
        "intent": "test.pending.request",
        "risk_level": "high",
        "latest_user_message": "帮我处理一下",
        "approval": {"id": "approval-1"},
        "pending_action": PENDING_ACTION,
    }
    state.update(overrides)
    return state


class FakeApproval:
    def __init__(
        self,
        *,
        status: str = "approved",
        decided_by: str | None = "approver-9",
        pending_action: str | None = SYSTEM_ACTION,
        consumed_at: float | None = None,
        created_at: float | None = None,
        user_id: str = "user-1",
    ) -> None:
        self.status = status
        self.decided_by = decided_by
        self.resume_payload = {"pending_action": pending_action} if pending_action else {}
        self.consumed_at = consumed_at
        self.created_at = created_at if created_at is not None else time.time()
        self.user_id = user_id


class FakeApprovalStore:
    def __init__(self, task: FakeApproval | None, error: Exception | None = None) -> None:
        self._task = task
        self._error = error

    async def get(self, task_id):
        if self._error is not None:
            raise self._error
        return self._task


@pytest.fixture
def system_wiring(monkeypatch, wiring):
    def install(task, error=None):
        monkeypatch.setattr(sw, "approval_store", FakeApprovalStore(task, error))
        return task

    wiring["install_approval"] = install
    return wiring


def system_state(**overrides):
    """The elevation path: the requester holds no write permission of their own."""
    state = {
        "request_id": "req-sys",
        "turn_id": "turn-sys",
        "user_id": "user-1",
        "user_context": {"roles": ["network_user", "employee"]},
        "selected_agent": "network",
        "intent": "test.system.request",
        "risk_level": "high",
        "latest_user_message": "帮我开通一下",
        "approval": {"id": "approval-1", "status": "approved"},
        "pending_action": SYSTEM_ACTION,
    }
    state.update(overrides)
    return state


def merge_state(**overrides):
    """The real production action: a staff member merges two tickets, gated by approval."""
    state = {
        "request_id": "req-merge",
        "turn_id": "turn-merge",
        "user_id": "staff-1",
        "user_context": {"roles": ["it_staff", "employee"]},
        "selected_agent": "it",
        "intent": "support.ticket.merge",
        "risk_level": "high",
        "latest_user_message": "帮我把 #12 合并到 #14",
        "approval": {"id": "approval-merge"},
        "pending_action": "ticket_merge",
    }
    state.update(overrides)
    return state


# --------------------------------------------------------------------------- registry

# The registry's production contents are asserted in tests/test_action_registry.py, which does
# not install the synthetic actions this file needs.


def test_outcome_text_separates_recorded_from_executed():
    integrated = ACTIONS["ticket_merge"]
    text, error = describe_outcome(integrated, ticket_number=None)
    assert error is None
    assert "已完成" in text

    pending = ACTIONS[PENDING_ACTION]
    text, error = describe_outcome(pending, ticket_number=77)
    assert error == NOT_IMPLEMENTED
    assert "77" in text
    assert "未执行" in text


# --------------------------------------------------------------------------- system authority


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"approval_id": None}, APPROVAL_MISSING),
        ({"approval_status": None}, APPROVAL_NOT_FOUND),
        ({"approval_status": "pending"}, APPROVAL_NOT_APPROVED),
        ({"approval_status": "rejected"}, APPROVAL_NOT_APPROVED),
        ({"decided_by": None}, APPROVAL_NOT_AUTHORIZED),
        ({"approval_action": "ticket_merge"}, APPROVAL_ACTION_MISMATCH),
        ({"approval_action": None}, None),  # nothing recorded to compare against
        ({"consumed_at": 1.0}, APPROVAL_ALREADY_CONSUMED),
        ({"created_at": 0.0}, APPROVAL_EXPIRED),
        ({"decided_by": "user-1"}, APPROVAL_SELF_DECIDED),
        ({"approval_requester": "someone-else"}, APPROVAL_REQUESTER_MISMATCH),
    ],
)
def test_every_way_an_approval_could_be_abused_is_refused(override, expected):
    facts = {
        "approval_id": "approval-1",
        "approval_status": "approved",
        "decided_by": "approver-9",
        "approval_action": SYSTEM_ACTION,
        "consumed_at": None,
        "created_at": time.time(),
        "approval_requester": "user-1",
        "requester": "user-1",
    }
    facts.update(override)

    decision = evaluate_system_authority(
        ACTIONS[SYSTEM_ACTION], now=time.time(), ttl_seconds=3600, **facts
    )

    if expected is None:
        assert decision.allowed is True
    else:
        assert decision.allowed is False, facts
        assert decision.reason == expected, facts


def test_refusal_text_says_nothing_was_executed():
    for reason in (
        APPROVAL_MISSING,
        APPROVAL_NOT_FOUND,
        APPROVAL_NOT_APPROVED,
        APPROVAL_NOT_AUTHORIZED,
        APPROVAL_ACTION_MISMATCH,
        APPROVAL_ALREADY_CONSUMED,
        APPROVAL_EXPIRED,
        APPROVAL_SELF_DECIDED,
        APPROVAL_REQUESTER_MISMATCH,
    ):
        text = refusal_message(ACTIONS[SYSTEM_ACTION], reason)
        assert "未执行" in text
        assert reason in text


# --------------------------------------------------------------------------- real merge


@pytest.mark.asyncio
async def test_the_real_merge_executes_under_the_approval(wiring):
    result = await sw.execute_approved_action(merge_state())

    write = result["executed_writes"][0]
    assert write["success"] is True
    assert write["toolName"] == "ticket_merge"
    assert "ticket_merge" in wiring["adapter"].names()
    # Nothing else was needed: the merge *is* the record.
    assert "ticket_create" not in wiring["adapter"].names()


@pytest.mark.asyncio
async def test_a_real_merge_is_recorded_as_succeeded_and_will_not_run_twice(wiring):
    first = await sw.execute_approved_action(merge_state())
    assert first["executed_writes"][0]["success"] is True
    calls_after_first = len(wiring["adapter"].calls)

    # An irreversible effect must not be applied twice, so the ledger's succeeded row has to
    # short-circuit the second attempt.
    second = await sw.execute_approved_action(merge_state())

    assert second["executed_writes"][0]["deduplicated"] is True
    assert len(wiring["adapter"].calls) == calls_after_first


@pytest.mark.asyncio
async def test_a_merge_needs_the_merge_permission(wiring):
    result = await sw.execute_approved_action(
        merge_state(user_context={"roles": ["employee"]})
    )

    assert result["status"] == "failed"
    assert "RBAC denied" in result["guard_error"]
    assert wiring["adapter"].calls == []
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "denied"
    assert writes[0]["detail"]["reason"] == "RBAC_DENIED"


@pytest.mark.asyncio
async def test_a_refused_merge_is_reported_without_claiming_success(wiring):
    wiring["adapter"].merge_result = {
        "success": False,
        "text": "不能把工单合并到它自己",
        "error": "MERGE_SELF",
    }

    result = await sw.execute_approved_action(merge_state())

    write = result["executed_writes"][0]
    assert write["success"] is False
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "failure"
    assert writes[0]["detail"]["external_executed"] is False


@pytest.mark.asyncio
async def test_a_merge_is_audited_as_a_requester_action(wiring):
    await sw.execute_approved_action(merge_state())

    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    detail = writes[0]["detail"]
    assert detail["executed_as"] == "requester"
    assert detail["authorizer_id"] is None  # dual control needs no separate authorizer
    assert detail["external_executed"] is True
    assert detail["approval_id"] == "approval-merge"


# --------------------------------------------------------------------------- system execution


@pytest.mark.asyncio
async def test_a_valid_approval_lets_the_system_execute_for_a_requester_who_cannot(
    wiring, system_wiring
):
    system_wiring["install_approval"](FakeApproval())

    result = await sw.execute_approved_action(system_state())

    assert "RBAC denied" not in result.get("guard_error", "")
    # The external action still honestly reports as not executed.
    assert result["executed_writes"][0]["error"] == NOT_IMPLEMENTED
    assert "ticket_create" in wiring["adapter"].names()


@pytest.mark.asyncio
async def test_a_system_execution_records_who_authorized_it(wiring, system_wiring):
    system_wiring["install_approval"](FakeApproval(decided_by="approver-9"))

    await sw.execute_approved_action(system_state())

    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    detail = writes[0]["detail"]
    assert writes[0]["outcome"] == "failure"  # the external action did not run
    assert detail["executed_as"] == "system"
    assert detail["authorizer_id"] == "approver-9"
    assert detail["on_behalf_of"] == "user-1"
    assert detail["external_executed"] is False


@pytest.mark.asyncio
async def test_an_approval_for_one_action_cannot_be_spent_on_another(wiring, system_wiring):
    """The nastiest replay: approve "merge two tickets", then have the system do something else.

    The action is read from the approval record, so the graph state cannot substitute a different
    one -- and an outright disagreement is refused and audited with both names.
    """
    system_wiring["install_approval"](FakeApproval(pending_action="ticket_merge"))

    result = await sw.execute_approved_action(system_state())

    assert result["status"] == "failed"
    assert result["guard_error"] == APPROVAL_ACTION_MISMATCH
    assert "未执行" in result["final_answer"]
    assert wiring["adapter"].calls == []
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "denied"
    assert writes[0]["detail"]["approved_action"] == "ticket_merge"
    assert writes[0]["detail"]["requested_action"] == SYSTEM_ACTION


@pytest.mark.asyncio
async def test_the_approval_record_decides_which_action_runs(wiring, system_wiring):
    # The state carries no action at all; the approval does. The approved action must still be
    # the one that runs, because that is what a human signed off on.
    system_wiring["install_approval"](FakeApproval(pending_action="ticket_merge"))
    state = merge_state(pending_action="")
    system_wiring["install_approval"](FakeApproval(pending_action="ticket_merge"))

    result = await sw.execute_approved_action(state)

    assert result["executed_writes"][0]["toolName"] == "ticket_merge"
    assert "ticket_merge" in wiring["adapter"].names()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task,expected",
    [
        (None, APPROVAL_NOT_FOUND),
        (FakeApproval(status="pending"), APPROVAL_NOT_APPROVED),
        (FakeApproval(decided_by=None), APPROVAL_NOT_AUTHORIZED),
        (FakeApproval(consumed_at=1.0), APPROVAL_ALREADY_CONSUMED),
        (FakeApproval(created_at=0.0), APPROVAL_EXPIRED),
        (FakeApproval(decided_by="user-1"), APPROVAL_SELF_DECIDED),
        (FakeApproval(user_id="someone-else"), APPROVAL_REQUESTER_MISMATCH),
    ],
)
async def test_an_invalid_approval_never_reaches_a_target_system(
    wiring, system_wiring, task, expected
):
    system_wiring["install_approval"](task)

    result = await sw.execute_approved_action(system_state())

    assert result["status"] == "failed"
    assert result["guard_error"] == expected
    assert "未执行" in result["final_answer"]
    assert wiring["adapter"].calls == []
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "denied"
    assert writes[0]["detail"]["reason"] == expected
    assert writes[0]["detail"]["executed_as"] == "system"


@pytest.mark.asyncio
async def test_an_unreadable_approval_store_is_not_an_authorization(wiring, system_wiring):
    system_wiring["install_approval"](None, error=RuntimeError("store down"))

    result = await sw.execute_approved_action(system_state())

    assert result["status"] == "failed"
    assert "未执行" in result["final_answer"]
    assert wiring["adapter"].calls == []


# --------------------------------------------------------------------------- not integrated


@pytest.mark.asyncio
async def test_a_declared_but_unintegrated_action_never_reaches_its_adapter(wiring):
    result = await sw.execute_approved_action(base_state())

    write = result["executed_writes"][0]
    assert write["success"] is False
    assert write["error"] == NOT_IMPLEMENTED
    assert write["externalExecuted"] is False
    # The requested action did not happen, so the adapter was not asked to run it...
    assert PENDING_ACTION not in wiring["adapter"].names()
    # ...but the achievable half did, for real.
    assert "ticket_create" in wiring["adapter"].names()
    assert write["changeTicketNumber"] == 4321


@pytest.mark.asyncio
async def test_the_audit_says_an_unintegrated_action_did_not_run(wiring):
    await sw.execute_approved_action(base_state())

    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "failure"
    assert writes[0]["detail"]["external_executed"] is False
    assert writes[0]["detail"]["change_ticket_number"] == 4321


@pytest.mark.asyncio
async def test_change_ticket_recording_can_be_disabled(monkeypatch, wiring):
    monkeypatch.setattr(settings, "high_risk_records_change_ticket", False)

    result = await sw.execute_approved_action(base_state())

    assert wiring["adapter"].calls == []
    write = result["executed_writes"][0]
    assert write["changeTicketNumber"] is None
    assert "未登记变更单" in write["text"]
    assert write["success"] is False


@pytest.mark.asyncio
async def test_an_approved_request_with_nothing_to_execute_is_still_registered(wiring):
    # The intents whose target system is not integrated resolve to no action at all. The
    # approval must still leave a trace rather than vanishing.
    result = await sw.execute_approved_action(
        base_state(pending_action="", intent="network.vpn.request", selected_agent="network")
    )

    write = result["tool_results"][0]
    assert write["noActionRequired"] is True
    assert write["changeTicketNumber"] == 4321
    assert "已登记变更单" in write["text"]
    assert "ticket_create" in wiring["adapter"].names()
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["detail"]["reason"] == "NO_WIRED_ACTION"


# --------------------------------------------------------------------------- containment


def test_the_synthetic_actions_are_not_in_any_agent_tool_list():
    # The production containment assertion (that ticket_merge is not selectable) lives in
    # test_action_registry.py; this one covers the test-only actions.
    from app.tools.selection import TOOLS_BY_AGENT

    for agent, tools in TOOLS_BY_AGENT.items():
        assert SYSTEM_ACTION not in tools, agent
        assert PENDING_ACTION not in tools, agent


def test_the_deleted_write_names_no_longer_exist_anywhere():
    import asyncio

    from app.tools.actions import ACTIONS

    # The actions themselves are gone (test_action_registry.py asserts the registry contents);
    # and the retired tool names cannot be called either, because no adapter implements them.
    for name in ("vpn_api_provision", "finance_submit", "approval_decision", "idp", "erp"):
        assert name not in ACTIONS
        result = asyncio.run(RefusingToolAdapter().execute(name, {}, {}))
        assert result["success"] is False
        assert "未执行" in result["text"]


def test_the_shared_arg_allow_list_was_not_widened_for_every_tool():
    allowed = screen_tool_input("ticket_create", {"type": "change", "title": "t", "description": "d"})
    assert allowed.allowed is True
    merge = screen_tool_input("ticket_merge", {"sourceNumber": 12, "targetNumber": 14})
    assert merge.allowed is True

    refused = screen_tool_input("mdm", {"type": "change", "title": "t"})
    assert refused.allowed is False
    assert refused.reason == "TOOL_ARGUMENT_NOT_ALLOWED"


# --------------------------------------------------------------------------- verify node


@pytest.mark.asyncio
async def test_verify_result_does_not_claim_completion_after_a_failure():
    events: list[dict[str, Any]] = []

    async def emit(event):
        events.append(event)

    await sw.verify_result(
        {"executed_writes": [{"success": False, "text": "尚未接入，未执行。", "error": NOT_IMPLEMENTED}]},
        {"configurable": {"emit": emit}},
    )

    assert "未成功执行" in events[0]["content"]
    assert "已完成" not in events[0]["content"]


@pytest.mark.asyncio
async def test_verify_result_reports_a_successful_write_as_such():
    events: list[dict[str, Any]] = []

    async def emit(event):
        events.append(event)

    await sw.verify_result(
        {"executed_writes": [{"success": True}]}, {"configurable": {"emit": emit}}
    )

    assert "已执行" in events[0]["content"]


def test_a_merge_without_two_ticket_numbers_is_refused_with_a_usable_message():
    from app.tools.adapters.ticket import MergeTargetsMissing, TicketToolAdapter

    adapter = TicketToolAdapter(base_url="http://java.test:8080")

    with pytest.raises(MergeTargetsMissing) as raised:
        adapter._tool_args("ticket_merge", {"latest_user_message": "帮我把工单合并一下"}, {})

    assert "未执行合并" in str(raised.value)
    assert "#12" in str(raised.value)  # the message shows the expected shape


@pytest.mark.asyncio
async def test_a_merge_reads_the_two_numbers_in_the_order_they_were_mentioned():
    from app.tools.adapters.ticket import TicketToolAdapter

    adapter = TicketToolAdapter(base_url="http://java.test:8080")

    args = adapter._tool_args(
        "ticket_merge", {"latest_user_message": "帮我把 #12 合并到 #14"}, {}
    )
    # "把 A 合并到 B" means source A, target B -- the order matters, they are not symmetric.
    assert args == {"sourceNumber": 12, "targetNumber": 14}


@pytest.mark.asyncio
async def test_a_merge_refusal_becomes_a_result_rather_than_an_exception(monkeypatch):
    from app.tools.adapters.ticket import MERGE_TARGETS_MISSING, TicketToolAdapter

    class ExplodingClient:
        async def post(self, *args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("no call should be made without two tickets")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "unit-test-secret")
    adapter = TicketToolAdapter(
        client_factory=lambda: ExplodingClient(), base_url="http://java.test:8080"
    )

    result = await adapter.execute(
        "ticket_merge",
        {"latest_user_message": "帮我合并工单"},
        {"user_id": "u1", "user_context": {"roles": ["it_staff"]}},
    )

    assert result["success"] is False
    assert result["error"] == MERGE_TARGETS_MISSING
    assert "未执行" in result["text"]
