from typing import Any

import httpx

from app.config import settings
from app.tools.adapters.contract import ToolAdapter
from app.tools.adapters.mock import execute_mock_tool
from app.tools.adapters.ticket import TICKET_TOOLS, TicketToolAdapter


class MockToolAdapter(ToolAdapter):
    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return await execute_mock_tool(tool_name, args, state)


class OpenApiToolAdapter(ToolAdapter):
    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-User-ID": str(state.get("user_id", "")),
        }
        if settings.tool_api_token:
            headers["Authorization"] = f"Bearer {settings.tool_api_token}"

        # Forward the ledger key so the provider can deduplicate a replayed call.
        idempotency_key = args.get("idempotency_key")
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.tool_base_url.rstrip('/')}/{tool_name}",
                headers=headers,
                json={"args": args, "state": state},
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                return {"success": True, "text": str(body)}
            return body


def get_tool_adapter() -> ToolAdapter:
    """The adapter the graph uses.

    Returns a router rather than a single adapter, because the tool set is mixed:
    the ticket tools call the Java service for real while the rest are still stubs.
    Routing here (instead of at every call site) keeps that split in one place and
    lets :func:`tool_status` report it.
    """
    return RoutingToolAdapter()


class RoutingToolAdapter(ToolAdapter):
    """Dispatches each tool to the adapter that actually implements it."""

    def __init__(
        self,
        ticket_adapter: ToolAdapter | None = None,
        fallback: ToolAdapter | None = None,
    ) -> None:
        self._ticket = ticket_adapter or TicketToolAdapter()
        self._fallback = fallback or MockToolAdapter()

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name in TICKET_TOOLS:
            return await self._ticket.execute(tool_name, args, state)
        result = await self._fallback.execute(tool_name, args, state)
        # Mark stub output as simulated so nothing downstream can mistake it for a real
        # integration. Every non-ticket tool is still a stub today.
        if isinstance(result, dict):
            return {**result, "simulated": True}
        return result


def tool_provider(tool_name: str) -> str:
    return "real" if tool_name in TICKET_TOOLS else "mock"


def tool_status() -> dict[str, Any]:
    """Per-tool truthfulness report for the readiness payload.

    An operator should be able to see which capabilities are integrations and which
    are placeholders without reading the source. Three sets matter and they are not
    the same thing:

    ``implemented``
        an adapter exists that talks to a real system;
    ``declared_mock``
        the registry offers the tool to an agent, but the adapter is a stub;
    ``implemented_not_wired``
        an adapter exists but no agent can reach it yet, so it is capability we own
        and have not exposed.
    """
    from app.registry.registry import list_agents

    declared: list[str] = []
    for agent in list_agents():
        for name in agent.get("tools", []):
            if name not in declared:
                declared.append(name)

    implemented = sorted(TICKET_TOOLS)
    declared_real = sorted(name for name in declared if name in TICKET_TOOLS)
    declared_mock = sorted(name for name in declared if name not in TICKET_TOOLS)
    not_wired = sorted(name for name in TICKET_TOOLS if name not in declared)

    return {
        "implemented": implemented,
        "declared_real": declared_real,
        "declared_mock": declared_mock,
        "implemented_not_wired": not_wired,
        "counts": {
            "implemented": len(implemented),
            "declared_real": len(declared_real),
            "declared_mock": len(declared_mock),
        },
        # No model function-calling: tools are chosen by rules over the user message.
        "routing": "rule-based",
        "java_service_configured": bool(settings.java_service_base_url),
    }
