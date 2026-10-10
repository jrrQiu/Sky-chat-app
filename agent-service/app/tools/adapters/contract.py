"""The tool adapter contract.

Kept in its own module so concrete adapters can implement it without importing the
registry that imports them. A cycle between ``base`` and ``ticket`` is easy to
introduce and shows up as an order-dependent import failure, which is a miserable
thing to debug.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Any

#: The idempotency key for the call currently being executed.
#:
#: Passed as a context variable rather than a parameter because the ledger mints it *after*
#: the adapter is chosen (the key is derived from the effect that ran), and because a
#: parameter would have to be threaded through every adapter and every test that calls one.
#: The value is stable for the whole call, including retries, which is the property that
#: makes it usable as a provider-side deduplication key.
_idempotency_key: ContextVar[str | None] = ContextVar("tool_idempotency_key", default=None)


def current_idempotency_key() -> str | None:
    return _idempotency_key.get()


def set_idempotency_key(value: str | None):
    """Set the key for this call. Returns the token to reset with."""
    return _idempotency_key.set((value or "").strip() or None)


def reset_idempotency_key(token) -> None:
    _idempotency_key.reset(token)


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
