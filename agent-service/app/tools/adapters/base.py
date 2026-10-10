"""Tool adapters and the routing between them.

The mock provider is gone. It used to answer twenty-odd tools with hardcoded strings, and every
one of those answers read like a successful integration. Now a tool that has no adapter simply
cannot be called: an unknown tool resolves to a refusal, which the caller reports honestly. A
result can therefore no longer claim something happened when nothing did.
"""

from typing import Any

import httpx

from app.config import settings
from app.registry.registry import list_agents
from app.tools.adapters.contract import ToolAdapter, current_idempotency_key
from app.tools.adapters.ticket import TICKET_TOOLS, TicketToolAdapter
from app.tools.catalog import TOOLS


class RefusingToolAdapter(ToolAdapter):
    """The answer for a tool with no implementation: refuse, and say nothing was done."""

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": False,
            "text": f"工具 {tool_name} 尚未实现，未执行任何操作。",
            "error": "TOOL_NOT_IMPLEMENTED",
        }


class OpenApiToolAdapter(ToolAdapter):
    """POSTs ``{tool_base_url}/{tool_name}`` and expects ``{success, text, ...}``.

    Kept for the day a real third-party system is integrated, and **unused** until then:
    ``tool_provider`` defaults to ``openapi`` only when a base URL is configured, and nothing is
    configured. It is plumbing, not an integration.
    """

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.tool_base_url:
            return await RefusingToolAdapter().execute(tool_name, args, state)

        headers = {
            "X-User-ID": str(state.get("user_id") or ""),
            # T3: the same key the first-party adapter sends, taken from the same place.
            "Idempotency-Key": str(
                args.get("idempotency_key") or current_idempotency_key() or ""
            ),
            "Content-Type": "application/json",
        }
        if settings.tool_api_token:
            headers["Authorization"] = f"Bearer {settings.tool_api_token}"

        url = f"{settings.tool_base_url.rstrip('/')}/{tool_name}"
        try:
            async with httpx.AsyncClient(timeout=settings.tool_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json={"args": args, "state": state})
        except httpx.HTTPError as error:
            return {
                "success": False,
                "text": f"目标系统不可用（{type(error).__name__}），该操作未执行",
                "error": "TOOL_PROVIDER_UNAVAILABLE",
            }
        if response.status_code >= 400:
            return {
                "success": False,
                "text": f"目标系统返回 {response.status_code}，该操作未执行",
                "error": "TOOL_PROVIDER_ERROR",
            }
        try:
            body = response.json()
        except ValueError:
            return {"success": False, "text": "目标系统返回了无法解析的响应", "error": "TOOL_PROVIDER_ERROR"}
        if not isinstance(body, dict):
            return {"success": False, "text": "目标系统返回了非预期结构", "error": "TOOL_PROVIDER_ERROR"}
        body.setdefault("success", False)
        return body


class RoutingToolAdapter(ToolAdapter):
    """Dispatches each tool to the adapter that implements it."""

    def __init__(
        self,
        ticket_adapter: ToolAdapter | None = None,
        fallback: ToolAdapter | None = None,
    ) -> None:
        self._ticket = ticket_adapter or TicketToolAdapter()
        self._fallback = fallback or (
            OpenApiToolAdapter()
            if settings.tool_provider == "openapi" and settings.tool_base_url
            else RefusingToolAdapter()
        )

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name in TICKET_TOOLS:
            return await self._ticket.execute(tool_name, args, state)
        return await self._fallback.execute(tool_name, args, state)


def get_tool_adapter() -> ToolAdapter:
    return RoutingToolAdapter()


def tool_status() -> dict[str, Any]:
    """Per-tool truthfulness report for the readiness payload.

    ``implemented`` is the catalog; ``declared`` is what the registry offers to an agent. A tool
    can be declared but not implemented (it will refuse), and implemented but not declared (it is
    capability we own and have not exposed) -- two different things, both worth seeing.
    """
    declared: list[str] = []
    for agent in list_agents():
        for name in agent.get("tools", []):
            if name not in declared:
                declared.append(name)

    implemented = sorted(TOOLS)
    openapi = bool(settings.tool_provider == "openapi" and settings.tool_base_url)
    return {
        "implemented": implemented,
        "declared": sorted(declared),
        "declared_unimplemented": sorted(n for n in declared if n not in TOOLS),
        "implemented_not_wired": sorted(n for n in implemented if n not in declared),
        "counts": {"implemented": len(implemented), "declared": len(declared)},
        # There is no mock provider any more: an unimplemented tool refuses rather than
        # pretending. This says which adapter serves the implemented ones.
        "adapters": {
            "ticket": "internal-http",
            "others": "openapi" if openapi else "refuse",
        },
    }
