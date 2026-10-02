"""The tool adapter contract.

Kept in its own module so concrete adapters can implement it without importing the
registry that imports them. A cycle between ``base`` and ``ticket`` is easy to
introduce and shows up as an order-dependent import failure, which is a miserable
thing to debug.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolAdapter(ABC):
    """Executes one tool call and returns a ``{success, text, ...}`` result.

    Failure is a normal return value, not an exception: the agent needs to be able to
    tell the user "the ticket service is unavailable" rather than surface a crash. An
    adapter must therefore never raise for a business refusal, and must never return a
    fabricated success when it could not reach the system it claims to have changed.
    """

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
