"""The server-supplied context envelope (Java -> Python).

This is the contract between "who can query the database" and "who decides what
goes in the prompt". Java owns the *candidates* because only Java can read the
business database; Python owns the *decision* because only Python can count the
tokens and see the provider's usage. The envelope therefore carries material, not
budgets.

`schema_version=2` adds the structured summary and the declared limits. Version 1
envelopes are still accepted: the missing fields default, so an older Java
deployment degrades to "no summary" rather than failing to parse.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: The version this engine writes/expects. Older envelopes are accepted.
ENVELOPE_SCHEMA_VERSION = 2


class ContextPolicy(BaseModel):
    """Policy as declared inside an envelope.

    Accepted for backward compatibility only. The engine's `policy.py` is the
    single source of truth for weight/floor/ceiling: an envelope cannot raise a
    block's priority by asserting one, because two sources of truth is how the
    Java `task_slots=50` and the Python `BLOCK_WEIGHTS` drifted apart.
    """

    min_tokens: int = 0
    max_tokens: int = 100_000
    truncation: str = "tail"


class ContextBlock(BaseModel):
    id: str
    kind: str
    priority: int = 0
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    policy: ContextPolicy = Field(default_factory=ContextPolicy)


class ContextSummary(BaseModel):
    """The previous rolling summary, with the range it already covers.

    `covered_until_message_id` is what keeps the summary and the raw window from
    overlapping: the engine only summarises messages *after* it.
    """

    version: int = 0
    covered_until_message_id: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    quality: str = ""


class ContextEnvelopeLimits(BaseModel):
    """How much material Java chose to send. Java's own bound, not the budget."""

    recent_messages: int = 0


class ContextEnvelope(BaseModel):
    schema_version: int = ENVELOPE_SCHEMA_VERSION
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[ContextBlock] = Field(default_factory=list)
    summary: ContextSummary | None = None
    limits: ContextEnvelopeLimits | None = None
