from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextPolicy(BaseModel):
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


class ContextEnvelope(BaseModel):
    schema_version: int = 1
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[ContextBlock] = Field(default_factory=list)
