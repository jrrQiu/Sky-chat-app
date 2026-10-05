"""What an approved high-risk action actually does.

Before this existed, `prepare_approval` guessed a `pending_action` from the *agent*
(`network` → `vpn_api_provision`, everything else → `approval_decision`) and the mock
adapter answered every one of them with a hardcoded success string: "账号和配置已开通",
"报销单已提交并进入付款流程". The approval was real; the execution was prose.

Every action declared here is now **real**. The two fictional ones (`vpn_api_provision`,
`finance_submit`) were deleted once it became clear that connecting them would have meant
writing an HTTP client against an API that does not exist and calling it an integration.
A request whose target system is not integrated no longer gets an action at all, and the
approved request is registered as a change ticket instead -- see
`service_workflow.execute_approved_action`.

This registry makes the facts separate and queryable:

``external_implemented``
    whether the target system is actually integrated. For an action that is not, the honest
    outcome is "approved, not executed" rather than a fabricated success.
``records_change_ticket``
    whether we register the approved work in the system we *do* own, so an approval leaves an
    auditable trail even when the external call cannot be made.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Who is authorized to perform an approved action.
#:
#: ``REQUESTER`` -- dual control. The requester must already hold the write permission, and the
#: approval adds a second signature. An approval can never grant a permission the requester did
#: not have, so approving cannot be used to escalate.
#:
#: ``SYSTEM`` -- the system performs the action on the strength of a valid approval. This is the
#: "employee requests, administrator approves, system provisions" model: an employee cannot
#: provision VPN access themselves, and the approval is the control that authorizes it. Because
#: this *is* an elevation path, `evaluate_system_authority` gates it on a valid, unconsumed,
#: unexpired approval that names this exact action and was decided by somebody other than the
#: requester.
AUTHORITY_REQUESTER = "requester"
AUTHORITY_SYSTEM = "system"


@dataclass(frozen=True)
class WriteAction:
    name: str
    description: str
    external_implemented: bool = False
    records_change_ticket: bool = False
    authority: str = AUTHORITY_REQUESTER


#: Actions keyed by the intent that requires them. The mapping is by intent rather than by
#: agent because an agent can serve several intents and only some of them write anything.
INTENT_ACTIONS: dict[str, str] = {
    "support.ticket.merge": "ticket_merge",
}

ACTIONS: dict[str, WriteAction] = {
    "ticket_merge": WriteAction(
        name="ticket_merge",
        description="合并两张工单（源工单关闭并指向目标，通信记录迁移）",
        # Implemented for real: it calls the Java ticket service through the internal tool
        # endpoint. The effect is irreversible -- the source becomes a tombstone -- which is
        # exactly why a second signature is required.
        external_implemented=True,
        # The merge itself is the record; there is nothing else to register.
        records_change_ticket=False,
        # `TicketService.merge` calls `requireAgent`, so the acting identity must be staff. The
        # requester therefore already holds the permission and the approval is dual control,
        # rather than the system acting for someone who could not do it themselves.
        authority=AUTHORITY_REQUESTER,
    ),
}

#: The approval itself is the outcome; there is nothing further to execute.
NO_ACTION_INTENTS = frozenset({"approval.decision.request"})

NOT_IMPLEMENTED = "ACTION_NOT_IMPLEMENTED"

# --- refusal reasons for a system-executed action -------------------------------------------
APPROVAL_MISSING = "APPROVAL_MISSING"
APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
APPROVAL_NOT_APPROVED = "APPROVAL_NOT_APPROVED"
APPROVAL_NOT_AUTHORIZED = "APPROVAL_NOT_AUTHORIZED"
APPROVAL_ACTION_MISMATCH = "APPROVAL_ACTION_MISMATCH"
APPROVAL_ALREADY_CONSUMED = "APPROVAL_ALREADY_CONSUMED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
APPROVAL_SELF_DECIDED = "APPROVAL_SELF_DECIDED"
APPROVAL_REQUESTER_MISMATCH = "APPROVAL_REQUESTER_MISMATCH"


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    reason: str | None = None
    authorizer_id: str | None = None


def evaluate_system_authority(
    action: WriteAction,
    *,
    approval_id: str | None,
    approval_status: str | None,
    decided_by: str | None,
    approval_action: str | None,
    consumed_at: float | None,
    created_at: float | None,
    approval_requester: str | None,
    requester: str | None,
    now: float,
    ttl_seconds: int,
) -> AuthorityDecision:
    """Decide whether an approval authorizes the system to perform ``action``.

    Every check below exists because of a specific way an approval could be abused to make the
    system perform something nobody authorized:

    * the approval must exist and the store -- not the graph state -- must say ``approved``, so a
      stale or reverted decision cannot be replayed;
    * somebody must actually be named as the decider;
    * the approval must have been created for *this* action, so approving "reset my password"
      cannot be spent on "grant VPN access";
    * it must not already be consumed, so one approval buys one effect;
    * it must not be expired, so an old approval is not a standing warrant;
    * the decider must differ from the requester: self-approval would make the whole control
      decorative;
    * the approval must belong to the requester whose turn is running, so an approval cannot be
      borrowed into somebody else's session.
    """
    if action.authority != AUTHORITY_SYSTEM:
        # Dual control needs no approval to *authorize* the action; the requester's own
        # permission is what authorizes it.
        return AuthorityDecision(allowed=True)

    if not approval_id:
        return AuthorityDecision(False, APPROVAL_MISSING)
    if approval_status is None:
        # An id was carried but the store has nothing for it: a stale or lost reference, which
        # is worth telling apart from "still pending" when someone has to investigate.
        return AuthorityDecision(False, APPROVAL_NOT_FOUND)
    if approval_status != "approved":
        return AuthorityDecision(False, APPROVAL_NOT_APPROVED)
    if not decided_by:
        return AuthorityDecision(False, APPROVAL_NOT_AUTHORIZED)
    if approval_action and approval_action != action.name:
        return AuthorityDecision(False, APPROVAL_ACTION_MISMATCH)
    if consumed_at is not None:
        return AuthorityDecision(False, APPROVAL_ALREADY_CONSUMED)
    if created_at is not None and ttl_seconds > 0 and now - created_at > ttl_seconds:
        return AuthorityDecision(False, APPROVAL_EXPIRED)
    if requester and decided_by == requester:
        return AuthorityDecision(False, APPROVAL_SELF_DECIDED)
    if approval_requester and requester and approval_requester != requester:
        return AuthorityDecision(False, APPROVAL_REQUESTER_MISMATCH)

    return AuthorityDecision(True, authorizer_id=decided_by)


def refusal_message(action: WriteAction, reason: str) -> str:
    """User-facing explanation for a refused system action. Says plainly that nothing ran."""
    explanation = {
        APPROVAL_MISSING: "没有可用的审批记录",
        APPROVAL_NOT_FOUND: "找不到该审批记录（引用可能已失效）",
        APPROVAL_NOT_APPROVED: "审批尚未通过",
        APPROVAL_NOT_AUTHORIZED: "审批没有记录授权人",
        APPROVAL_ACTION_MISMATCH: "审批针对的动作与本次要执行的动作不一致",
        APPROVAL_ALREADY_CONSUMED: "该审批已被使用过（一次审批只能执行一次）",
        APPROVAL_EXPIRED: "审批已过期",
        APPROVAL_SELF_DECIDED: "审批由申请人自己做出",
        APPROVAL_REQUESTER_MISMATCH: "该审批不属于当前申请人",
    }.get(reason, "审批校验未通过")
    return f"未执行「{action.description}」：{explanation}（{reason}）。"

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
                # Who the action is authorized as. `system` means the approval is the control,
                # which is the highest-risk configuration and should be visible as such.
                "authority": action.authority,
            }
            for action in ACTIONS.values()
        ],
        "intentsWithoutAction": sorted(NO_ACTION_INTENTS),
        "externalExecutionAvailable": any(a.external_implemented for a in ACTIONS.values()),
        "systemExecutedActions": sorted(
            a.name for a in ACTIONS.values() if a.authority == AUTHORITY_SYSTEM
        ),
    }
