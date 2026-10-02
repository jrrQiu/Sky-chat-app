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
from typing import Any

import pytest

from app.config import settings
from app.core.effect_ledger import EffectLedger
from app.core.guardrails import screen_tool_input
from app.graph.nodes import service_workflow as sw
from app.tools import actions as actions_module
from app.tools.actions import (
    ACTIONS,
    NO_ACTION,
    NOT_IMPLEMENTED,
    action_key,
    action_status,
    describe_outcome,
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
    state = {
        "request_id": "req-1",
        "turn_id": "turn-1",
        "user_id": "user-1",
        "user_context": {"roles": ["network_admin", "employee"]},
        "selected_agent": "network",
        "intent": "network.vpn.request",
        "risk_level": "high",
        "latest_user_message": "我要在外网访问财务系统，帮我申请 VPN",
        "approval": {"id": "approval-1"},
        "pending_action": "vpn_api_provision",
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
async def test_rbac_still_gates_the_write(wiring):
    result = await sw.execute_approved_action(
        base_state(user_context={"roles": ["employee"]})
    )

    assert result["status"] == "failed"
    assert "RBAC denied" in result["guard_error"]
    assert wiring["adapter"].calls == []


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
