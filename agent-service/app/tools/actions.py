"""What an approved high-risk action actually does.

Before this existed, `prepare_approval` guessed a `pending_action` from the *agent*
(`network` → `vpn_api_provision`, everything else → `approval_decision`) and the mock
adapter answered every one of them with a hardcoded success string: "账号和配置已开通",
"报销单已提交并进入付款流程". The approval was real; the execution was prose.

This registry makes the two facts separate and queryable:

``external_implemented``
    whether the target system is actually integrated. For every action today this is
    **False**, so the honest outcome of an approved action is "approved, not executed"
    rather than a fabricated success.
``records_change_ticket``
    whether we register the approved work in the system we *do* own. This part is real:
    a row lands in the Java ticket table, so an approval leaves an auditable trail even
    when the external call cannot be made.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WriteAction:
    name: str
    description: str
    external_implemented: bool = False
    records_change_ticket: bool = False


#: Actions keyed by the intent that requires them. The mapping is by intent rather than by
#: agent because an agent can serve several intents and only some of them write anything.
INTENT_ACTIONS: dict[str, str] = {
    "network.vpn.request": "vpn_api_provision",
    "finance.service.submit": "finance_submit",
}

ACTIONS: dict[str, WriteAction] = {
    "vpn_api_provision": WriteAction(
        name="vpn_api_provision",
        description="在 VPN 系统开通账号与访问配置",
        external_implemented=False,
        records_change_ticket=True,
    ),
    "finance_submit": WriteAction(
        name="finance_submit",
        description="在 ERP 提交报销单并进入付款流程",
        external_implemented=False,
        records_change_ticket=True,
    ),
}

#: The approval itself is the outcome; there is nothing further to execute.
NO_ACTION_INTENTS = frozenset({"approval.decision.request"})

NOT_IMPLEMENTED = "ACTION_NOT_IMPLEMENTED"

#: Key used when an approved request has no external action. It appears in the approval key,
#: so it must be produced by one function: `prepare_approval` and the approval task the tool
#: layer creates both derive it here, and if they disagreed the same turn would end up with
#: two different approval tasks.
NO_ACTION = "no-action"


def action_key(intent: str | None) -> str:
    """The stable action name for an intent, used to key the approval task."""
    action = resolve(intent)
    return action.name if action else NO_ACTION


def resolve(intent: str | None) -> WriteAction | None:
    """The action an approved request needs, or ``None`` when there is nothing to run.

    ``None`` is a real answer, not a failure: approving an approval-decision request has
    no external side effect, and inventing one was how the fake success text got in.
    """
    if not intent:
        return None
    if intent in NO_ACTION_INTENTS:
        return None
    name = INTENT_ACTIONS.get(intent)
    return ACTIONS.get(name) if name else None


def describe_outcome(action: WriteAction, ticket_number: int | None) -> tuple[str, str | None]:
    """The user-facing text plus an error code when part of the action could not run.

    Composition matters: an operator (and the user) must be able to tell "we recorded this"
    from "we did this".
    """
    recorded = (
        f"已登记变更单 #{ticket_number}" if ticket_number is not None else "未登记变更单"
    )
    if action.external_implemented:
        return f"{action.description} 已完成；{recorded}。", None
    return (
        f"审批已通过，{recorded}；但「{action.description}」尚未接入目标系统，"
        f"该动作未执行。",
        NOT_IMPLEMENTED,
    )


def action_status() -> dict[str, object]:
    """Truthfulness report for the readiness payload."""
    return {
        "actions": [
            {
                "name": action.name,
                "description": action.description,
                "externalImplemented": action.external_implemented,
                "recordsChangeTicket": action.records_change_ticket,
            }
            for action in ACTIONS.values()
        ],
        "intentsWithoutAction": sorted(NO_ACTION_INTENTS),
        "externalExecutionAvailable": any(a.external_implemented for a in ACTIONS.values()),
    }
