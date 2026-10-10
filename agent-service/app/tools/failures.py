"""One place that decides what a tool failure *means*.

Two consumers used to keep their own string sets -- the metrics outcome in
``app.observability.tool_metrics`` and the circuit breaker in ``graph/nodes/tools`` -- and they
disagreed. A code that was missing from both fell through as a generic failure **and** was not
counted towards the breaker, so a dependency could fail on every single round while the breaker
stayed closed and the loop kept paying for it. ``INVALID_INTERNAL_CALLER`` was in exactly that
position: a persistently rejected caller identity never opened a breaker.

``classify`` is now the only answer, and ``tests/test_tool_failures.py`` pins every code the
adapters and the loop can emit, so adding a code without classifying it fails a test instead of
silently degrading.

The rule for ``transport`` is the same one the Java ``AgentCircuitBreaker.isSafeToRetry``
encodes, and it is deliberately narrow: only *the dependency failing* counts. A refusal we
produced ourselves (RBAC, a rail, a missing argument) means the dependency is healthy, so it
must not open the breaker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Outcomes, mirrored from ``app.observability.tool_metrics``. Duplicated as literals rather
#: than imported so that this module stays dependency-free (it is imported by both sides).
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_REFUSED = "refused"
OUTCOME_DENIED = "denied"
OUTCOME_UNAVAILABLE = "unavailable"

#: Codes whose failure means "we never got a usable answer out of the dependency".
_PROVIDER_CODES = "provider"

#: Who can make the next attempt succeed. This is what separates a deterministic failure the
#: user can fix ("that ticket number does not exist") from a dependency failure nobody can fix
#: by answering a question ("the ticket service is down").
RECOVER_NONE = "none"
RECOVER_ARGUMENTS = "arguments"
RECOVER_PERMISSION = "permission"

# ---------------------------------------------------------------------------
# Model-facing recovery contract.
#
# `recoverable` above answers "can asking the user help?". The model needs a
# different question answered: "what do I do now?". Those are not the same -- a
# tool that is not in this agent's catalog is not something the user can fix, yet
# the model absolutely must change what it does next (pick another tool).
#
# The point of putting this in the contract is that the decision stops being
# something the model infers. The model has no way to know whether a write landed,
# or whether a call is safe to repeat; the code does. Sending the *conclusion*
# rather than the raw situation is what turns "the model guesses" into "the model
# follows".
# ---------------------------------------------------------------------------
RECOVERY_NONE = "none"
RECOVERY_RETRY = "retry"
RECOVERY_CHANGE_TOOL = "change_tool"
RECOVERY_CHANGE_ARGUMENTS = "change_arguments"
RECOVERY_ASK_USER = "ask_user"

#: What happened to the effect this call may have produced.
SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureAdvice:
    """The model-facing answer to one failure, decided by the error code alone."""

    reason: str
    #: Whether repeating *this exact call* is safe. Safety, not usefulness: a
    #: connection failure is safe to repeat, but if the adapter already exhausted
    #: its retries, repeating it is pointless -- `guidance` says so.
    retryable: bool
    side_effect: str
    recovery: str
    guidance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "retryable": self.retryable,
            "sideEffect": self.side_effect,
            "recovery": self.recovery,
            "guidance": self.guidance,
        }


#: Coded advice per error code. Every field is written down per code on purpose:
#: "retryable" is not derived from `transport` or from the message text, because a
#: derivation is exactly where two callers start disagreeing.
_ADVICE: dict[str, FailureAdvice] = {
    # --- the dependency never received the request: replay cannot duplicate -------
    "TOOL_PROVIDER_UNAVAILABLE": FailureAdvice(
        OUTCOME_UNAVAILABLE, True, SIDE_EFFECT_NONE, RECOVERY_RETRY,
        "目标系统未能连通，本次未产生任何变更。可以稍后重试，或改用其他方式。",
    ),
    # --- the dependency answered with an error: what it applied is unknown -------
    "TOOL_PROVIDER_ERROR": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "目标系统返回了错误，无法确认变更是否已生效。不要重试，先向用户说明情况。",
    ),
    "TICKET_SERVICE_UNAVAILABLE": FailureAdvice(
        OUTCOME_UNAVAILABLE, True, SIDE_EFFECT_NONE, RECOVERY_RETRY,
        "工单服务未能连通，本次未产生任何变更。可以稍后重试。",
    ),
    "TICKET_SERVICE_ERROR": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "工单服务返回了错误，无法确认变更是否已生效。不要重试。",
    ),
    "TOOL_CALL_TIMEOUT": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "调用超时，无法确认变更是否已生效。不要重试，先向用户说明情况。",
    ),
    "RESULT_UNKNOWN": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "写操作被中断，目标系统的状态未知。不要重试，也不要声称操作已完成。",
    ),
    "INVALID_INTERNAL_CALLER": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "服务间身份校验失败，这是一个部署配置问题，重试无用。请告知管理员。",
    ),
    # --- refused locally: nothing was attempted, and no retry will change that ----
    "EFFECT_IN_PROGRESS": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "同一个操作正在进行中，本次未重复执行。不要重试，稍后再查结果。",
    ),
    "TOOL_CIRCUIT_OPEN": FailureAdvice(
        OUTCOME_UNAVAILABLE, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "该依赖连续失败已被熔断保护，本次未执行。不要重试，直接向用户说明暂时不可用。",
    ),
    "CALLER_TOKEN_UNAVAILABLE": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "缺少服务间签名密钥，无法调用目标系统。这是一个配置问题，重试无用。",
    ),
    "RBAC_DENIED": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_ASK_USER,
        "当前用户的角色没有该工具权限，换参数或换工具都无法绕过。"
        "请告知用户需要申请权限。",
    ),
    "FORBIDDEN_TICKET_ACTION": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_ASK_USER,
        "只有工单负责人可以执行该操作。请告知用户需要由负责人处理。",
    ),
    "INTERNAL_NOTE_FORBIDDEN": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_ASK_USER,
        "只有工单负责人可以添加内部备注。请告知用户。",
    ),
    # --- the tool itself is not usable here: pick another one --------------------
    "TOOL_UNKNOWN": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_TOOL,
        "该工具不存在于工具目录中。请从提供的工具列表里另选一个，不要重复调用它。",
    ),
    "AGENT_TOOL_NOT_ALLOWED": FailureAdvice(
        OUTCOME_DENIED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_TOOL,
        "当前角色/场景不允许使用该工具。请改选一个可用工具完成任务，不要重复调用它。",
    ),
    "APPROVAL_GATED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_TOOL,
        "该工具只能通过审批流程执行，不能在对话中直接调用。"
        "请改用其他方式，或告知用户需要走审批。",
    ),
    "TOOL_NOT_IMPLEMENTED": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_TOOL,
        "该工具尚未实现，调用不会产生任何效果。请改选其他可用工具。",
    ),
    # --- the request was wrong: a different value may work -----------------------
    "GUARDRAIL_REFUSED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "参数被安全护栏拒绝，本次未执行。请改用允许的参数，或换一种方式完成。",
    ),
    "MERGE_TARGETS_MISSING": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "没有指明要合并的两张工单。请先向用户确认工单编号。",
    ),
    "TOOL_REQUEST_REJECTED": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "目标系统拒绝了这次请求（参数不正确）。请修正参数后重试。",
    ),
    "TOOL_TARGET_NOT_FOUND": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "请求的对象不存在。请核对编号；无法确定时向用户确认，不要编造编号。",
    ),
    "TICKET_NOT_FOUND": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "工单不存在或当前用户不可见。请向用户确认编号，不要编造编号。",
    ),
    "SPLIT_COMMENT_NOT_FOUND": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "要拆分的评论不存在。请核对后重试。",
    ),
    "INVALID_TICKET_TYPE": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "工单类型不在允许范围内。请改用 allowed 的类型。",
    ),
    "INVALID_TICKET_STATUS": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "工单状态不在允许范围内。请改用允许的状态。",
    ),
    "INVALID_LINK_TYPE": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "关联类型不在允许范围内。请改用允许的类型。",
    ),
    "INVALID_REQUEST": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "请求格式不正确。请检查参数后重试。",
    ),
    "TOOL_ARGUMENTS_MISSING": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "请求缺少必要的标识参数。请从用户消息中取得编号，或向用户询问。",
    ),
    "MERGE_SELF": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "不能把工单合并到它自己。请确认两个不同的编号。",
    ),
    "LINK_SELF": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "不能把工单关联到它自己。请确认两个不同的编号。",
    ),
    "MERGE_CYCLE": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "该合并会形成循环。请改用其他目标工单。",
    ),
    "DUPLICATE_CALL_REFUSED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_CHANGE_ARGUMENTS,
        "同样的调用已经执行过一次，本次被忽略。"
        "若要取得不同结果，请改变参数或改用其他工具，不要原样重复。",
    ),
    "RESULT_SCHEMA_VIOLATION": FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "工具声称成功但缺少必需字段，本次结果不可信。"
        "不要据此推断编号或结论，也不要重试。",
    ),
    # --- the requested state is already the outcome: explain, do not retry -------
    "INVALID_TRANSITION": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "工单已经处于该状态，无需再执行。请直接向用户说明当前状态。",
    ),
    "LINK_DUPLICATE": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "该关联已经存在，无需再执行。请直接告知用户。",
    ),
    "TICKET_ALREADY_MERGED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_NONE,
        "该工单已经被合并，无需再执行。请直接告知用户。",
    ),
    # --- waiting on the user: not a failure to work around -----------------------
    "CONFIRMATION_REQUIRED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_ASK_USER,
        "该写操作正在等待用户确认，尚未执行。不要声称已经完成，也不要重复提交。",
    ),
    "CONFIRMATION_REJECTED": FailureAdvice(
        OUTCOME_REFUSED, False, SIDE_EFFECT_NONE, RECOVERY_ASK_USER,
        "用户拒绝了这次写操作，未执行。"
        "这通常意味着用户想要的并不是这个动作：请先确认用户的真实意图，"
        "不要原样再调用一次。",
    ),
}



@dataclass(frozen=True)
class FailureClass:
    """What one error code means: its metric outcome, its blame, and who can fix it."""

    outcome: str
    #: True only when the dependency itself failed. Counting our own refusals would open the
    #: breaker because of a caller bug -- the mistake the Java side documents avoiding too.
    transport: bool
    note: str = ""
    #: ``RECOVER_ARGUMENTS`` when a different value would work, ``RECOVER_PERMISSION`` when an
    #: authorisation step is needed, ``RECOVER_NONE`` when a retry cannot help.
    recoverable: str = RECOVER_NONE

    @property
    def needs_user_input(self) -> bool:
        return self.recoverable in {RECOVER_ARGUMENTS, RECOVER_PERMISSION}


FAILURES: dict[str, FailureClass] = {
    # --- the dependency failed: these count towards the breaker -----------------------------
    "TOOL_PROVIDER_UNAVAILABLE": FailureClass(
        OUTCOME_UNAVAILABLE, True, "the target system could not be reached"
    ),
    "TOOL_PROVIDER_ERROR": FailureClass(
        OUTCOME_UNAVAILABLE, True, "the target system answered with an error or an unusable body"
    ),
    "TICKET_SERVICE_UNAVAILABLE": FailureClass(
        OUTCOME_UNAVAILABLE, True, "the Java service could not be reached"
    ),
    "TICKET_SERVICE_ERROR": FailureClass(
        OUTCOME_UNAVAILABLE, True, "the Java service answered with an error or an unusable body"
    ),
    "TOOL_CALL_TIMEOUT": FailureClass(
        OUTCOME_UNAVAILABLE, True, "our per-call deadline fired; the dependency is too slow"
    ),
    "RESULT_UNKNOWN": FailureClass(
        OUTCOME_UNAVAILABLE,
        True,
        "a write was interrupted, so the dependency's state is unknown",
    ),
    "INVALID_INTERNAL_CALLER": FailureClass(
        OUTCOME_DENIED,
        True,
        "the Java service rejected our caller identity: every call fails until the "
        "two shared secrets match, so the breaker should stop the retries",
    ),
    # --- we refused, or the dependency is fine and the fault is ours: never count -----------
    "EFFECT_IN_PROGRESS": FailureClass(
        OUTCOME_UNAVAILABLE, False, "an identical effect is already in flight"
    ),
    "TOOL_CIRCUIT_OPEN": FailureClass(
        OUTCOME_UNAVAILABLE, False, "refused locally because the breaker is open"
    ),
    "CALLER_TOKEN_UNAVAILABLE": FailureClass(
        OUTCOME_DENIED, False, "no signing secret is configured, so Java cannot be called"
    ),
    "RBAC_DENIED": FailureClass(
        OUTCOME_DENIED,
        False,
        "the caller's roles do not grant the tool",
        recoverable=RECOVER_PERMISSION,
    ),
    "AGENT_TOOL_NOT_ALLOWED": FailureClass(
        OUTCOME_DENIED, False, "the tool is not in the agent's catalog entry"
    ),
    "APPROVAL_GATED": FailureClass(
        OUTCOME_REFUSED,
        False,
        "an approval-gated tool is never run from the conversation",
        recoverable=RECOVER_PERMISSION,
    ),
    "GUARDRAIL_REFUSED": FailureClass(
        OUTCOME_REFUSED, False, "a rail refused the arguments or the tool's output"
    ),
    "CONFIRMATION_REQUIRED": FailureClass(
        OUTCOME_REFUSED, False, "a confirm-tier write is waiting for the user"
    ),
    "CONFIRMATION_REJECTED": FailureClass(
        OUTCOME_REFUSED, False, "the user declined the write"
    ),
    "DUPLICATE_CALL_REFUSED": FailureClass(
        OUTCOME_REFUSED, False, "the same call with the same arguments already ran"
    ),
    "MERGE_TARGETS_MISSING": FailureClass(
        OUTCOME_REFUSED,
        False,
        "the request did not name the two tickets to merge",
        recoverable=RECOVER_ARGUMENTS,
    ),
    # --- deterministic: our request was wrong, so the dependency is fine. Never count ------
    "TOOL_REQUEST_REJECTED": FailureClass(
        OUTCOME_FAILED,
        False,
        "the dependency rejected the request itself (4xx): the arguments are wrong, not the "
        "service. Treating this as a dependency failure would open the breaker because of a "
        "caller bug",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "TOOL_TARGET_NOT_FOUND": FailureClass(
        OUTCOME_FAILED,
        False,
        "the object the request names does not exist (404): the caller or the user can supply "
        "a different identifier",
        recoverable=RECOVER_ARGUMENTS,
    ),
    # --- business refusals from the Java tool service ---------------------------------------
    # Java answers a refused tool call with **HTTP 200** and ``{"success": false, "error": code}``
    # (see ``InternalToolController.failure``), so these codes arrive on the *successful* HTTP
    # path. Without an entry here they were unclassified: the user hit a dead end and the loop
    # could not ask for the value that would work -- which is the whole point of slot filling.
    # The codes are the ``TicketService`` constants.
    "TICKET_NOT_FOUND": FailureClass(
        OUTCOME_FAILED,
        False,
        "the ticket does not exist or is not visible to this caller (Java conflates the two on "
        "purpose, so the tool's own text carries the nuance)",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "SPLIT_COMMENT_NOT_FOUND": FailureClass(
        OUTCOME_FAILED,
        False,
        "the comment to split does not exist",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "INVALID_TICKET_TYPE": FailureClass(
        OUTCOME_FAILED,
        False,
        "the tool was given a ticket type outside the allowed set",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "INVALID_TICKET_STATUS": FailureClass(
        OUTCOME_FAILED,
        False,
        "the tool was given a ticket status outside the allowed set",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "INVALID_LINK_TYPE": FailureClass(
        OUTCOME_FAILED,
        False,
        "the tool was given a link type outside the allowed set",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "INVALID_REQUEST": FailureClass(
        OUTCOME_FAILED, False, "the request itself was malformed",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "TOOL_ARGUMENTS_MISSING": FailureClass(
        OUTCOME_FAILED,
        False,
        "the request did not carry an identifier the tool needs (Java emits this on HTTP 200)",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "MERGE_SELF": FailureClass(
        OUTCOME_REFUSED, False, "a ticket cannot be merged into itself",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "LINK_SELF": FailureClass(
        OUTCOME_REFUSED, False, "a ticket cannot be linked to itself",
        recoverable=RECOVER_ARGUMENTS,
    ),
    "MERGE_CYCLE": FailureClass(
        OUTCOME_REFUSED, False, "that merge would form a cycle",
        recoverable=RECOVER_ARGUMENTS,
    ),
    # Deterministic refusals that a different answer would not fix: the model explains them.
    "INVALID_TRANSITION": FailureClass(
        OUTCOME_REFUSED, False, "the ticket is already in that state"
    ),
    "LINK_DUPLICATE": FailureClass(OUTCOME_REFUSED, False, "that link already exists"),
    "TICKET_ALREADY_MERGED": FailureClass(
        OUTCOME_REFUSED, False, "the ticket has already been merged"
    ),
    "FORBIDDEN_TICKET_ACTION": FailureClass(
        OUTCOME_DENIED,
        False,
        "only the assignee may act on this ticket's state",
        recoverable=RECOVER_PERMISSION,
    ),
    "INTERNAL_NOTE_FORBIDDEN": FailureClass(
        OUTCOME_DENIED,
        False,
        "only the assignee may add an internal note",
        recoverable=RECOVER_PERMISSION,
    ),
    "RESULT_SCHEMA_VIOLATION": FailureClass(
        OUTCOME_FAILED, False, "the tool reported success without the fields it promised"
    ),
    "TOOL_NOT_IMPLEMENTED": FailureClass(
        OUTCOME_FAILED, False, "no adapter implements this tool"
    ),
    "TOOL_UNKNOWN": FailureClass(
        OUTCOME_FAILED, False, "the model asked for a tool that is not in the catalog at all"
    ),
}


def classify(error: str | None) -> FailureClass:
    """The class of one error code. Unknown codes are reported, not guessed at.

    An unknown code returns a plain failure that does **not** count towards the breaker: the
    safe default for something nobody has classified is to keep calling, and the warning below
    is what makes the omission visible instead of silent.
    """
    if not error:
        return FailureClass(OUTCOME_FAILED, False, "no error code was set")
    known = FAILURES.get(error)
    if known is not None:
        return known
    logger.warning(
        "Unclassified tool error code %r: counted as a plain failure and NOT towards the "
        "circuit breaker. Add it to app/tools/failures.py.",
        error,
    )
    return FailureClass(OUTCOME_FAILED, False, f"unclassified code {error!r}")


def outcome_for(error: str | None) -> str:
    return classify(error).outcome


def counts_towards_breaker(error: str | None) -> bool:
    return classify(error).transport


def known_codes() -> frozenset[str]:
    return frozenset(FAILURES)


_RECOVERY_HINTS = {
    RECOVER_ARGUMENTS: "请补充或更正相关信息后重试。",
    RECOVER_PERMISSION: (
        "当前账号没有该操作的权限。请提交权限申请或联系管理员授权后重试。"
    ),
}


def recovery_message(error: str | None, tool_text: str = "") -> str | None:
    """The sentence to show the user for a recoverable failure, or ``None``.

    ``None`` means "asking the user cannot help": the dependency failed, or the failure is not
    something a different answer would fix.

    A recoverable-by-arguments failure usually explains itself ("name the two tickets to
    merge"), so the tool's own text is preferred. A permission failure's text is written for
    us rather than for the user ("RBAC denied tool ... for current user roles"), so the
    canonical hint wins there.
    """
    cls = classify(error)
    if not cls.needs_user_input:
        return None
    hint = _RECOVERY_HINTS.get(cls.recoverable, "")
    if cls.recoverable == RECOVER_ARGUMENTS:
        return (tool_text or hint).strip() or None
    return hint or tool_text.strip() or None


def advice_for(error: str | None) -> FailureAdvice:
    """The model-facing advice for one error code.

    An unclassified code gets a conservative default: not safe to repeat, effect
    unknown, and an instruction to explain rather than to act. Saying "unknown, so
    do not retry" is the only honest answer when nobody has decided what the code
    means -- and `classify` has already logged the omission.
    """
    if error:
        known = _ADVICE.get(error)
        if known is not None:
            return known
    return FailureAdvice(
        OUTCOME_FAILED, False, SIDE_EFFECT_UNKNOWN, RECOVERY_NONE,
        "该错误未被分类，无法确认是否已生效。不要重试，请如实向用户说明。",
    )


def codes_without_advice() -> frozenset[str]:
    """Error codes in ``FAILURES`` with no model-facing advice entry.

    A gap here is not cosmetic: an un-advised code reaches the model as the generic
    default, which says "do not retry" for something that may well have been
    retryable. ``tests/test_tool_failures.py`` asserts this is empty, so adding a
    code without advice fails a test instead of quietly degrading.
    """
    return frozenset(FAILURES) - frozenset(_ADVICE)

