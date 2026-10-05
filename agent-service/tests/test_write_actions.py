"""High-risk execution: the approval is real, so the execution must not lie about itself.

Before this, an approved action was answered by a hardcoded success string ("账号和配置已
开通", "报销单已提交并进入付款流程"). The approval had genuinely happened, which is what
made the reply convincing. These tests pin the honest behaviour:

* an action whose target system is not integrated must **not** call the adapter and must
  report failure, so the ledger records a failed effect that can be retried once the target
  exists;
* the achievable half -- registering a change ticket in our own system -- really happens;
* the text distinguishes "we recorded this" from "we did this".
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
    APPROVAL_NOT_FOUND,
    APPROVAL_NOT_APPROVED,
    APPROVAL_NOT_AUTHORIZED,
    APPROVAL_REQUESTER_MISMATCH,
    APPROVAL_SELF_DECIDED,
    AUTHORITY_REQUESTER,
    AUTHORITY_SYSTEM,
    NO_ACTION,
    NOT_IMPLEMENTED,
    action_key,
    action_status,
    describe_outcome,
    evaluate_system_authority,
    refusal_message,
    resolve,
)
from app.tools.adapters.mock import execute_mock_tool, implemented_write_refusal


class RecordingAdapter:
    """Stands in for the tool layer; records the tool names it was asked to run."""

    def __init__(self, ticket_number: int | None = 4321) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ticket_number = ticket_number

    async def execute(self, tool_name, args, state):
        self.calls.append((tool_name, args))
        if tool_name == "ticket_create":
            return {
                "success": True,
                "text": f"已创建工单 #{self._ticket_number}",
                "data": {"number": self._ticket_number, "type": args.get("type")},
            }
        # Anything else has no target system; refuse rather than fabricate.
        return implemented_write_refusal(tool_name)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def wiring(monkeypatch):
    """Isolated ledger, recorded audit, and a recording adapter."""
    ledger = EffectLedger(postgres=None)
    monkeypatch.setattr(sw, "effect_ledger", ledger)

    recorded: list[dict[str, Any]] = []

    async def fake_record_action(action, **kwargs):
        recorded.append({"action": action, **kwargs})

    monkeypatch.setattr(sw, "record_action", fake_record_action)

    adapter = RecordingAdapter()

    def adapter_factory():
        return adapter

    monkeypatch.setattr(sw, "get_tool_adapter", adapter_factory)
    return {"adapter": adapter, "audit": recorded}


def base_state(**overrides):
    """A dual-control action: the requester holds the write permission themselves.

    The mechanics tests (change ticket, ledger, audit) use this so they exercise the write
    pipeline without also standing up an approval. The system-executed path has its own group
    of tests below.
    """
    state = {
        "request_id": "req-1",
        "turn_id": "turn-1",
        "user_id": "user-1",
        "user_context": {"roles": ["finance_user", "employee"]},
        "selected_agent": "finance",
        "intent": "finance.service.submit",
        "risk_level": "high",
        "latest_user_message": "我要提交一笔差旅报销发票",
        "approval": {"id": "approval-1"},
        "pending_action": "finance_submit",
    }
    state.update(overrides)
    return state


# --------------------------------------------------------------------------- registry


def test_the_action_is_derived_from_the_intent_not_the_agent():
    assert resolve("network.vpn.request").name == "vpn_api_provision"
    assert resolve("finance.service.submit").name == "finance_submit"


def test_requests_with_no_external_action_resolve_to_none():
    # Approving an approval-decision request has no external side effect. Inventing one is
    # how the fake success text got in.
    assert resolve("approval.decision.request") is None
    assert resolve("it.incident.report") is None
    assert resolve(None) is None
    assert resolve("") is None
    assert action_key("it.incident.report") == NO_ACTION


def test_no_action_reports_that_nothing_is_integrated():
    status = action_status()

    assert status["externalExecutionAvailable"] is False
    assert all(entry["externalImplemented"] is False for entry in status["actions"])
    assert "approval.decision.request" in status["intentsWithoutAction"]


def test_outcome_text_separates_recorded_from_executed():
    action = ACTIONS["vpn_api_provision"]

    text, error = describe_outcome(action, ticket_number=77)
    assert error == NOT_IMPLEMENTED
    assert "77" in text
    assert "未执行" in text
    # It must not claim the provisioning happened.
    assert "已开通" not in text

    integrated = dataclasses.replace(action, external_implemented=True)
    text, error = describe_outcome(integrated, ticket_number=None)
    assert error is None
    assert "已完成" in text


# --------------------------------------------------------------------------- the guard


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["vpn_api_provision", "finance_submit", "approval_decision"])
async def test_the_legacy_write_names_can_no_longer_fabricate_success(name):
    result = await execute_mock_tool(name, {}, {"intent": "network.vpn.request"})

    assert result["success"] is False
    assert result["error"] == "ACTION_NOT_IMPLEMENTED"
    assert "未执行" in result["text"]


def test_the_shared_arg_allow_list_was_not_widened_for_every_tool():
    # The ticket tools declare a schema; every other tool must still reject invented args.
    allowed = screen_tool_input("ticket_create", {"type": "change", "title": "t", "description": "d"})
    assert allowed.allowed is True

    refused = screen_tool_input("mdm", {"type": "change", "title": "t"})
    assert refused.allowed is False
    assert refused.reason == "TOOL_ARGUMENT_NOT_ALLOWED"


# --------------------------------------------------------------------------- execution


@pytest.mark.asyncio
async def test_an_unintegrated_action_never_reaches_the_adapter(wiring):
    result = await sw.execute_approved_action(base_state())

    write = result["executed_writes"][0]
    assert write["success"] is False
    assert write["error"] == NOT_IMPLEMENTED
    assert write["externalExecuted"] is False
    # The requested action did not happen, so the adapter must not have been asked to run it.
    assert "vpn_api_provision" not in wiring["adapter"].names()
    # The achievable half did happen, for real.
    assert "ticket_create" in wiring["adapter"].names()
    assert write["changeTicketNumber"] == 4321
    assert "4321" in write["text"]


@pytest.mark.asyncio
async def test_only_the_change_ticket_is_created_for_the_integrated_flow(wiring):
    await sw.execute_approved_action(base_state())

    _, args = wiring["adapter"].calls[0]
    assert args["type"] == "change"
    assert "变更" in args["title"]
    # The approval id is carried into the record, so the trail links back.
    assert "approval-1" in args["description"]
    assert "尚未接入" in args["description"]


@pytest.mark.asyncio
async def test_the_audit_record_says_the_external_action_did_not_run(wiring):
    await sw.execute_approved_action(base_state())

    writes = [entry for entry in wiring["audit"] if entry["action"] == "tool.write"]
    assert writes, "the write must be audited"
    assert writes[0]["outcome"] == "failure"
    assert writes[0]["detail"]["external_executed"] is False
    assert writes[0]["detail"]["change_ticket_number"] == 4321


@pytest.mark.asyncio
async def test_a_request_with_no_action_is_reported_as_such(wiring):
    result = await sw.execute_approved_action(
        base_state(intent="approval.decision.request", pending_action="", selected_agent="approval")
    )

    write = result["tool_results"][0]
    assert write["success"] is True
    assert write["noActionRequired"] is True
    assert "没有需要外部系统执行的动作" in write["text"]
    assert wiring["adapter"].calls == []


@pytest.mark.asyncio
async def test_change_ticket_recording_can_be_disabled(monkeypatch, wiring):
    monkeypatch.setattr(settings, "high_risk_records_change_ticket", False)

    result = await sw.execute_approved_action(base_state())

    assert wiring["adapter"].calls == []
    write = result["executed_writes"][0]
    assert write["changeTicketNumber"] is None
    assert "未登记变更单" in write["text"]
    # Still honest about the action itself.
    assert write["success"] is False
    assert write["error"] == NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_rbac_still_gates_a_dual_control_write(wiring):
    # Dual control means the requester's own permission authorizes the action.
    result = await sw.execute_approved_action(
        base_state(user_context={"roles": ["employee"]})
    )

    assert result["status"] == "failed"
    assert "RBAC denied" in result["guard_error"]
    assert wiring["adapter"].calls == []


# --------------------------------------------------------------------------- system authority


class FakeApproval:
    """The fields of an approval the authority check reads."""

    def __init__(
        self,
        *,
        status: str = "approved",
        decided_by: str | None = "approver-9",
        pending_action: str | None = "vpn_api_provision",
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


def vpn_state(**overrides):
    """The elevation path: the requester has no write permission of their own."""
    state = {
        "request_id": "req-vpn",
        "turn_id": "turn-vpn",
        "user_id": "user-1",
        # `network_user` only: it is the whole point that they cannot provision themselves.
        "user_context": {"roles": ["network_user", "employee"]},
        "selected_agent": "network",
        "intent": "network.vpn.request",
        "risk_level": "high",
        "latest_user_message": "我要在外网访问财务系统，帮我申请 VPN",
        "approval": {"id": "approval-1", "status": "approved"},
        "pending_action": "vpn_api_provision",
    }
    state.update(overrides)
    return state


@pytest.fixture
def system_wiring(monkeypatch, wiring):
    """`wiring` plus a controllable approval store."""
    def install(task, error=None):
        monkeypatch.setattr(sw, "approval_store", FakeApprovalStore(task, error))
        return task

    wiring["install_approval"] = install
    return wiring


def test_the_two_authority_models_are_explicit_per_action():
    assert ACTIONS["vpn_api_provision"].authority == AUTHORITY_SYSTEM
    assert ACTIONS["finance_submit"].authority == AUTHORITY_REQUESTER
    # Visible in the readiness payload, because the system-executed one is the risky one.
    assert action_status()["systemExecutedActions"] == ["vpn_api_provision"]


def test_a_dual_control_action_needs_no_approval_to_authorize_it():
    decision = evaluate_system_authority(
        ACTIONS["finance_submit"],
        approval_id=None,
        approval_status=None,
        decided_by=None,
        approval_action=None,
        consumed_at=None,
        created_at=None,
        approval_requester=None,
        requester="user-1",
        now=time.time(),
        ttl_seconds=3600,
    )
    assert decision.allowed is True


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"approval_id": None}, APPROVAL_MISSING),
        ({"approval_status": None}, APPROVAL_NOT_FOUND),
        ({"approval_status": "pending"}, APPROVAL_NOT_APPROVED),
        ({"approval_status": "rejected"}, APPROVAL_NOT_APPROVED),
        ({"decided_by": None}, APPROVAL_NOT_AUTHORIZED),
        ({"approval_action": "finance_submit"}, APPROVAL_ACTION_MISMATCH),
        ({"approval_action": None}, None),  # no recorded action means "nothing to compare"
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
        "approval_action": "vpn_api_provision",
        "consumed_at": None,
        "created_at": time.time(),
        "approval_requester": "user-1",
        "requester": "user-1",
    }
    facts.update(override)

    decision = evaluate_system_authority(
        ACTIONS["vpn_api_provision"],
        now=time.time(),
        ttl_seconds=3600,
        **facts,
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
        APPROVAL_ACTION_MISMATCH,
        APPROVAL_ALREADY_CONSUMED,
        APPROVAL_EXPIRED,
        APPROVAL_SELF_DECIDED,
        APPROVAL_REQUESTER_MISMATCH,
        APPROVAL_NOT_AUTHORIZED,
    ):
        text = refusal_message(ACTIONS["vpn_api_provision"], reason)
        assert "未执行" in text
        assert reason in text


@pytest.mark.asyncio
async def test_a_valid_approval_lets_the_system_execute_for_a_requester_who_cannot(wiring, system_wiring):
    system_wiring["install_approval"](FakeApproval())

    result = await sw.execute_approved_action(vpn_state())

    write = result["executed_writes"][0]
    # The requester holds no `network:vpn:create`; under the dual-control model this would have
    # been refused, which was the point of the change.
    assert "vpn_api_provision" not in write.get("error", "")
    assert "RBAC denied" not in result.get("guard_error", "")
    # The external action still honestly reports as not executed.
    assert write["error"] == NOT_IMPLEMENTED
    assert "ticket_create" in wiring["adapter"].names()


@pytest.mark.asyncio
async def test_a_system_execution_records_who_authorized_it(wiring, system_wiring):
    system_wiring["install_approval"](FakeApproval(decided_by="approver-9"))

    await sw.execute_approved_action(vpn_state())

    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "failure"  # the external action did not run
    detail = writes[0]["detail"]
    assert detail["executed_as"] == "system"
    assert detail["authorizer_id"] == "approver-9"
    assert detail["on_behalf_of"] == "user-1"
    assert detail["external_executed"] is False


@pytest.mark.asyncio
async def test_a_dual_control_write_is_not_recorded_as_system_executed(wiring):
    await sw.execute_approved_action(base_state())

    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["detail"]["executed_as"] == "requester"
    assert writes[0]["detail"]["authorizer_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task,expected",
    [
        (None, APPROVAL_NOT_FOUND),
        (FakeApproval(status="pending"), APPROVAL_NOT_APPROVED),
        (FakeApproval(decided_by=None), APPROVAL_NOT_AUTHORIZED),
        (FakeApproval(pending_action="finance_submit"), APPROVAL_ACTION_MISMATCH),
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

    result = await sw.execute_approved_action(vpn_state())

    assert result["status"] == "failed"
    assert result["guard_error"] == expected
    assert "未执行" in result["final_answer"]
    # Nothing may run: not the external action, not even the change ticket.
    assert wiring["adapter"].calls == []
    writes = [e for e in wiring["audit"] if e["action"] == "tool.write"]
    assert writes[0]["outcome"] == "denied"
    assert writes[0]["detail"]["reason"] == expected
    assert writes[0]["detail"]["executed_as"] == "system"


@pytest.mark.asyncio
async def test_an_unreadable_approval_store_is_not_an_authorization(wiring, system_wiring):
    system_wiring["install_approval"](None, error=RuntimeError("store down"))

    result = await sw.execute_approved_action(vpn_state())

    assert result["status"] == "failed"
    assert "未执行" in result["final_answer"]
    assert wiring["adapter"].calls == []


def test_the_system_action_is_not_reachable_from_the_normal_tool_path():
    """The elevation channel must be narrow: no ordinary tool selection may pick it up."""
    from app.tools.adapters.mock import TOOLS_BY_AGENT, select_tools_for_request

    for agent, tools in TOOLS_BY_AGENT.items():
        assert "vpn_api_provision" not in tools, agent

    for message in ("开通 VPN", "我要申请 VPN", "帮我开通外网访问"):
        for agent in ("network", "it", "orchestrator", "knowledge", "finance", "hr", "approval"):
            assert "vpn_api_provision" not in select_tools_for_request(agent, message), (agent, message)


# --------------------------------------------------------------------------- verify node


@pytest.mark.asyncio
async def test_verify_result_does_not_claim_completion_after_a_failure():
    events: list[dict[str, Any]] = []

    async def emit(event):
        events.append(event)

    config = {"configurable": {"emit": emit}}
    state = {
        "executed_writes": [
            {"success": False, "text": "VPN 尚未接入，未执行。", "error": NOT_IMPLEMENTED}
        ]
    }

    await sw.verify_result(state, config)

    content = events[0]["content"]
    assert "未成功执行" in content
    assert "已完成" not in content


@pytest.mark.asyncio
async def test_verify_result_reports_a_successful_write_as_such():
    events: list[dict[str, Any]] = []

    async def emit(event):
        events.append(event)

    await sw.verify_result({"executed_writes": [{"success": True}]}, {"configurable": {"emit": emit}})

    assert "已执行" in events[0]["content"]
