"""The first genuinely real tool: service-desk tickets.

Every other tool in this service is still a stub that returns canned text. These
four call the Java service for real, over the internal caller token, and the Java
side applies the *same* ticket rules a human request would go through -- visibility,
the internal-note filter, the state machine, the impact/urgency matrix. Nothing is
re-implemented here, so there is exactly one policy point.

Two things this module deliberately does **not** do:

* it never invents a result. If Java cannot be reached the tool reports failure and
  says the ticket service is unavailable, rather than claiming a ticket was created;
* it never lets the caller choose a priority, because the matrix owns that.

Argument synthesis is heuristic (regex over the user's message), for the same
reason tool *selection* is: this service has no model function-calling, so the
arguments are built here and that fact is reported in ``/ready`` as
``tool_routing: rule-based``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import httpx

from app.config import settings
from app.core.internal_token import CallerTokenUnavailable, mint_java_caller_token
from app.tools.adapters.base import ToolAdapter

#: Tools this adapter serves. Everything else still resolves to the mock adapter.
TICKET_TOOLS = (
    "ticket_create",
    "ticket_get",
    "ticket_list",
    "ticket_comment",
    "ticket_merge",
)

#: Ticket numbers in a message ("#12", "工单 12"). Used for merges, where the user names two.
_TICKET_NUMBER_PATTERN = re.compile(r"#\s*(\d+)|工单\s*(?:号|编号)?\s*(\d+)")

TICKET_SERVICE_UNAVAILABLE = "TICKET_SERVICE_UNAVAILABLE"
CALLER_TOKEN_UNAVAILABLE = "CALLER_TOKEN_UNAVAILABLE"
MERGE_TARGETS_MISSING = "MERGE_TARGETS_MISSING"


class MergeTargetsMissing(ValueError):
    """The request did not name two tickets, so there is nothing to merge.

    Raised while building the arguments and converted into a normal failure result: a request
    the user can fix is not an exception, it is an answer.
    """

_MAX_TITLE = 120
_MAX_DESCRIPTION = 2000

#: 故障词 → incident，申请词 → request。Order matters: a message that mentions both
#: ("打印机坏了，顺便申请个新墨盒") is filed as the fault, which is the actionable half.
_REQUEST_PATTERN = re.compile(r"申请|申领|开通|办理|配置|需要一?[个台套]|request", re.I)
_INCIDENT_PATTERN = re.compile(
    r"坏了|损坏|故障|报障|无法|不能|不可用|打不开|连不上|失败|异常|宕机|离线|报错|崩溃|"
    r"error|fail|broken|down",
    re.I,
)
#: High impact: whole-team or production impact rather than one person's device.
_HIGH_IMPACT_PATTERN = re.compile(
    r"全员|所有人|整个|全公司|全部|生产|线上|服务器|数据库|无法登录|大面积|紧急", re.I
)
_HIGH_URGENCY_PATTERN = re.compile(r"紧急|尽快|马上|立刻|急|asap|urgent", re.I)


class TicketToolAdapter(ToolAdapter):
    """Calls the Java service's internal tool endpoint."""

    def __init__(
        self,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        base_url: str | None = None,
    ) -> None:
        # Injectable so tests exercise the real request-building code without a network.
        self._client_factory = client_factory
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        return (self._base_url or settings.java_service_base_url or "").rstrip("/")

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = str(state.get("user_id") or "")
        roles = list((state.get("user_context") or {}).get("roles") or [])
        try:
            token = mint_java_caller_token(user_id, roles)
        except CallerTokenUnavailable:
            # A configuration problem, not a user-facing refusal: say so plainly instead of
            # pretending the action happened.
            return _failure(
                "工单服务未配置内部身份，无法执行该操作（请检查 AGENT_INTERNAL_JWT_SECRET）",
                CALLER_TOKEN_UNAVAILABLE,
            )
        except ValueError:
            return _failure("无法确定操作人身份，未执行该操作", CALLER_TOKEN_UNAVAILABLE)

        try:
            body_args = self._tool_args(tool_name, args, state)
        except MergeTargetsMissing as error:
            return _failure(str(error), MERGE_TARGETS_MISSING)
        payload = {"args": body_args}
        url = f"{self.base_url}/v1/internal/tools/{tool_name}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with self._client() as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as error:
            # Reported as a tool failure with a clear reason. Never a fabricated success.
            return _failure(
                f"工单服务暂时不可用（{type(error).__name__}），该操作未执行",
                TICKET_SERVICE_UNAVAILABLE,
            )

        if response.status_code == 401:
            return _failure(
                "工单服务拒绝了内部调用身份，该操作未执行（两侧密钥可能不一致）",
                "INVALID_INTERNAL_CALLER",
            )
        if response.status_code >= 400:
            return _failure(
                f"工单服务返回 {response.status_code}，该操作未执行",
                "TICKET_SERVICE_ERROR",
            )

        try:
            body = response.json()
        except ValueError:
            return _failure("工单服务返回了无法解析的响应", "TICKET_SERVICE_ERROR")

        if not isinstance(body, dict):
            return _failure("工单服务返回了非预期的响应结构", "TICKET_SERVICE_ERROR")
        body.setdefault("success", False)
        return body

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=settings.tool_timeout_seconds)

    # ------------------------------------------------------------------ arguments

    def _tool_args(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        message = str(args.get("latest_user_message") or state.get("latest_user_message") or "")
        if tool_name == "ticket_create":
            return self._create_args(args, state, message)
        if tool_name == "ticket_list":
            return _without_none({"status": args.get("status"), "type": args.get("type"),
                                  "limit": args.get("limit")})
        if tool_name == "ticket_get":
            return _without_none({"ticketId": args.get("ticketId"), "number": args.get("number")})
        if tool_name == "ticket_comment":
            return _without_none({
                "ticketId": args.get("ticketId"),
                "number": args.get("number"),
                "body": args.get("body") or message,
                "visibility": args.get("visibility") or "public",
            })
        if tool_name == "ticket_merge":
            return self._merge_args(args, message)
        return dict(args)

    def _merge_args(self, args: dict[str, Any], message: str) -> dict[str, Any]:
        """Which two tickets to merge.

        The user names them in prose ("把 #12 合并到 #14"), and this service has no model
        function-calling, so the numbers are read from the message. Explicit ids win when a
        caller supplies them.
        """
        explicit = _without_none({
            "sourceTicketId": args.get("sourceTicketId"),
            "targetTicketId": args.get("targetTicketId"),
            "sourceNumber": args.get("sourceNumber"),
            "targetNumber": args.get("targetNumber"),
        })
        if "sourceTicketId" in explicit or "targetTicketId" in explicit:
            return explicit

        numbers = [
            int(first or second)
            for first, second in _TICKET_NUMBER_PATTERN.findall(message)
        ]
        if len(numbers) < 2:
            # Refused locally with a usable message: sending a merge without both tickets would
            # only make the target service repeat this.
            raise MergeTargetsMissing(
                "请提供要合并的两张工单编号，例如「把 #12 合并到 #14」。未执行合并。"
            )
        # Order is the order they were mentioned: "把 A 合并到 B" means source A, target B.
        return {"sourceNumber": numbers[0], "targetNumber": numbers[1]}

    def _create_args(
        self,
        args: dict[str, Any],
        state: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        # The conversation id is carried so a ticket raised from chat stays traceable.
        conversation_id = (
            args.get("conversationId")
            or state.get("conversation_id")
            or state.get("conversationId")
        )
        return _without_none({
            "title": args.get("title") or _title_from(message),
            "description": args.get("description") or _truncate(message, _MAX_DESCRIPTION),
            "type": args.get("type") or infer_ticket_type(message),
            "impact": args.get("impact") or infer_impact(message),
            "urgency": args.get("urgency") or infer_urgency(message),
            "conversationId": conversation_id,
        })


def infer_ticket_type(message: str) -> str:
    """Fault vs request. A fault wins when both appear: it is the actionable half."""
    if _INCIDENT_PATTERN.search(message):
        return "incident"
    if _REQUEST_PATTERN.search(message):
        return "request"
    return "incident"


def infer_impact(message: str) -> str:
    return "high" if _HIGH_IMPACT_PATTERN.search(message) else "medium"


def infer_urgency(message: str) -> str:
    return "high" if _HIGH_URGENCY_PATTERN.search(message) else "medium"


def _title_from(message: str) -> str:
    first_line = (message or "").strip().splitlines()[0] if message else ""
    return _truncate(first_line, _MAX_TITLE) or "来自对话的工单"


def _truncate(value: str, limit: int) -> str:
    text = (value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _failure(text: str, code: str) -> dict[str, Any]:
    return {"success": False, "text": text, "error": code}
