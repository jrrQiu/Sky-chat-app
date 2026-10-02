from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import settings
from app.tools.adapters.mock import execute_mock_tool


class ToolAdapter(ABC):
    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


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
    if settings.tool_provider == "openapi" and settings.tool_base_url:
        return OpenApiToolAdapter()
    return MockToolAdapter()
