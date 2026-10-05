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
from typing import Any


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
            "在工单上追加一条回复。visibility=public 对申请人可见；"
            "visibility=internal 是内部备注，仅处理人可见。"
        ),
        parameters={
            "number": {"type": "integer", "description": "工单编号"},
            "ticketId": {"type": "string"},
            "body": {"type": "string", "description": "回复内容"},
            "visibility": {"type": "string", "enum": ["public", "internal"]},
        },
        required=("body",),
        permission="ticket:comment",
        writes=True,
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
            }
            for spec in TOOLS.values()
        ],
        "approvalGated": approval_gated_tools(),
        "modelCallable": direct_tools(),
    }
