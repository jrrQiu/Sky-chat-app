"""The tool catalog: the single description of every tool that exists.

Before this, the same fact was written down in four places -- the mock handler table, the
per-agent tool list, `TOOL_PERMISSIONS`, and the guardrail's argument allow-list -- and they
drifted. The drift was not theoretical: `ticket_merge` shipped with arguments the guardrail
rejected, so the approved action would have been blocked by its own safety rail.

Everything now derives from one entry:

* the permission required to call it (``registry/rbac.py``);
* the JSON Schema handed to the model (function calling);
* the argument allow-list the guardrail enforces;
* whether it may be executed directly or only after an approval.

**Only tools that talk to a real system belong here.** The previous catalog listed twenty-odd
tools for IdP, MDM, HRIS, ERP and friends that returned hardcoded strings; they are gone. A tool
that cannot do anything must not be offered to a model, because the model will call it and the
user will be told something happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: How a state-changing tool may be invoked. Declared per tool rather than derived from
#: `approval_gated`, because "it was not marked as needing approval" is not a decision --
#: it is the absence of one, and that is how the agent came to create tickets and post
#: public comments on a user's behalf with no confirmation step at all.
#:
#: * ``direct``   — the caller's own permission is the whole authorisation.
#: * ``confirm``  — one explicit user confirmation per call, via a graph interrupt.
#: * ``approval`` — a separate approver's decision, via the existing approval engine.
WritePolicy = Literal["direct", "confirm", "approval"]

WRITE_DIRECT: WritePolicy = "direct"
WRITE_CONFIRM: WritePolicy = "confirm"
WRITE_APPROVAL: WritePolicy = "approval"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    #: argument name -> JSON Schema fragment. Doubles as the guardrail allow-list.
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: The permission a caller must hold. Enforced in `registry/rbac.py`.
    permission: str = ""
    #: Approval-gated tools are never handed to the model: they are driven by the intent
    #: taxonomy so that the elevation path stays narrow and auditable.
    approval_gated: bool = False
    #: Whether the tool changes state. Used for reporting, not for authorization.
    writes: bool = False
    #: Which authorisation tier a write needs. Read-only tools leave it at `direct`, where
    #: it is not consulted.
    write_policy: WritePolicy = WRITE_DIRECT
    #: Dot-paths a *successful* result must contain, e.g. ``("data.number",)``. A tool that
    #: reports success without them is rewritten into a failure, so the model cannot take a
    #: success-shaped result with no ticket number and invent the number from context.
    result_required: tuple[str, ...] = ()
    #: Keys of the result's ``data`` object that may be echoed back into the model context.
    #: Everything else is dropped: a third-party system must not be able to pour an
    #: arbitrary structure into the prompt.
    result_data_fields: tuple[str, ...] = ()


TOOLS: dict[str, ToolSpec] = {
    "ticket_create": ToolSpec(
        name="ticket_create",
        description=(
            "为用户创建一张服务台工单。当用户报告故障、报修、申请资源或权限，"
            "而问题无法在对话中直接解决时使用。创建后返回工单编号。"
        ),
        parameters={
            "type": {"type": "string", "enum": ["incident", "request", "change"],
                     "description": "incident=故障，request=申请，change=变更"},
            "title": {"type": "string", "description": "一句话摘要，取自用户原话"},
            "description": {"type": "string", "description": "问题详情"},
            "impact": {"type": "string", "enum": ["low", "medium", "high"]},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        required=("title",),
        permission="ticket:create",
        writes=True,
        # Creating a ticket notifies other people and occupies a queue, so it asks once.
        write_policy=WRITE_CONFIRM,
        # Success without a number is not a success we can report: this is the contract
        # that stops the model from filling in a plausible ticket number.
        result_required=("data.number",),
        result_data_fields=("id", "number", "type", "status", "priority", "title"),
    ),
    "ticket_list": ToolSpec(
        name="ticket_list",
        description="列出当前用户自己的工单。当用户询问工单状态、进度，或想确认自己提交过什么时使用。",
        parameters={
            "status": {"type": "string", "description": "按状态过滤，例如 new/in_progress/resolved/closed"},
            "type": {"type": "string", "enum": ["incident", "request", "change"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数，默认 5"},
        },
        permission="ticket:read",
    ),
    "ticket_get": ToolSpec(
        name="ticket_get",
        description="按编号查看某张工单的详情（描述、状态、最近回复）。",
        parameters={
            "number": {"type": "integer", "description": "工单编号，例如用户说 #12 就传 12"},
            "ticketId": {"type": "string", "description": "工单内部 id（已知时优先传编号）"},
        },
        permission="ticket:read",
    ),
    "ticket_comment": ToolSpec(
        name="ticket_comment",
        description=(
            "在工单上追加一条**公开**回复，用于把用户自己说出的补充信息记录到工单上。"
            "只有当用户明确要求记录、并给出内容时才调用；不要替用户编造内容。"
        ),
        parameters={
            "number": {"type": "integer", "description": "工单编号"},
            "ticketId": {"type": "string"},
            # `visibility` is deliberately absent. The agent may only post public replies: an
            # internal note is a human action in the tickets UI, because "hidden from the
            # requester" is not something an assistant should be able to do on someone's behalf.
            # Removing it from the schema also means the guardrail rejects it if the model
            # invents it, rather than silently downgrading.
            "body": {"type": "string", "description": "回复内容，取自用户原话"},
        },
        required=("body",),
        permission="ticket:comment",
        writes=True,
        # Speaking in the user's name on a public thread cannot be taken back, so it asks
        # once per call.
        write_policy=WRITE_CONFIRM,
        result_required=("data.number",),
        result_data_fields=("id", "number", "status", "commentCount"),
    ),
    "ticket_merge": ToolSpec(
        name="ticket_merge",
        description=(
            "把一张工单合并到另一张（源工单关闭并指向目标，通信记录迁移）。"
            "此动作不可逆，必须经过审批，因此不交由模型直接调用。"
        ),
        parameters={
            "sourceNumber": {"type": "integer", "description": "被合并掉的工单编号"},
            "targetNumber": {"type": "integer", "description": "保留的工单编号"},
            "sourceTicketId": {"type": "string"},
            "targetTicketId": {"type": "string"},
        },
        permission="ticket:merge",
        approval_gated=True,
        writes=True,
        # Irreversible: the source ticket stops existing as an independent entity.
        write_policy=WRITE_APPROVAL,
        result_required=("data.source.number", "data.target.number"),
        result_data_fields=("source", "target"),
    ),
}


def arg_keys(name: str) -> frozenset[str]:
    """The declared argument names of a tool, used as the guardrail's allow-list."""
    spec = TOOLS.get(name)
    return frozenset(spec.parameters) if spec else frozenset()


def permission_for(name: str) -> str | None:
    spec = TOOLS.get(name)
    return spec.permission or None if spec else None


def direct_tools() -> list[str]:
    """Tools a model may call on its own (i.e. not approval-gated)."""
    return [name for name, spec in TOOLS.items() if not spec.approval_gated]


def approval_gated_tools() -> list[str]:
    return [name for name, spec in TOOLS.items() if spec.approval_gated]


def confirm_tools() -> list[str]:
    """Tools that pause for one explicit user confirmation."""
    return [
        name
        for name, spec in TOOLS.items()
        if spec.writes and spec.write_policy == WRITE_CONFIRM
    ]


def write_policy_for(name: str) -> WritePolicy:
    """The authorisation tier for a tool. Unknown tools are treated as the strictest."""
    spec = TOOLS.get(name)
    if spec is None:
        return WRITE_APPROVAL
    return spec.write_policy if spec.writes else WRITE_DIRECT


def result_contract(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(required dot-paths, allowed data fields)`` for a tool's result."""
    spec = TOOLS.get(name)
    if spec is None:
        return (), ()
    return spec.result_required, spec.result_data_fields


def dig(value: Any, path: str) -> Any:
    """Read a dot-path out of a nested mapping, or return ``None``."""
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def openai_schema(name: str) -> dict[str, Any]:
    """The tool definition as the OpenAI/DeepSeek chat API expects it."""
    spec = TOOLS[name]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": spec.parameters,
        # Additional properties are refused everywhere: the guardrail rejects unknown arguments,
        # so letting the model invent them would turn a schema violation into a runtime error.
        "additionalProperties": False,
    }
    if spec.required:
        parameters["required"] = list(spec.required)
    return {"type": "function", "function": {
        "name": spec.name,
        "description": spec.description,
        "parameters": parameters,
    }}


def openai_tools(names: list[str]) -> list[dict[str, Any]]:
    return [openai_schema(name) for name in names if name in TOOLS]


def catalog_status() -> dict[str, Any]:
    """Truthfulness report for the readiness payload."""
    return {
        "tools": [
            {
                "name": spec.name,
                "permission": spec.permission,
                "writes": spec.writes,
                "approvalGated": spec.approval_gated,
                # The authorisation tier is reported per tool: "the agent can write" is
                # not a useful statement, "which writes need what" is.
                "writePolicy": spec.write_policy,
                "resultRequired": list(spec.result_required),
            }
            for spec in TOOLS.values()
        ],
        "approvalGated": approval_gated_tools(),
        "confirmGated": confirm_tools(),
        "modelCallable": direct_tools(),
    }
