"""Context block policies: the single source of truth for budget behaviour.

Before this module the same fact was written in two places and had already
drifted: Java's envelope declared one set of priorities (``task_slots=50``) while
Python's ``BLOCK_WEIGHTS`` declared another. A block's weight, floor, ceiling,
protection level and truncation strategy are one decision, so they live in one
table.

Meaning of each field:

``weight``
    Share of the *contested* budget during water-filling. Weight is not an
    amount: an unused share flows back to the other blocks.
``min_tokens``
    Floor. For ``hard`` blocks this is a guarantee that must be met or the
    request fails; for the others it is only a starting point.
``max_tokens``
    Ceiling. Content beyond it is truncated (or dropped, for item-counted
    blocks) even when budget is free, so one block cannot crowd out the rest.
``protection``
    * ``hard`` — never truncated below ``min_tokens``; failing to fit raises
      ``BudgetInfeasible`` instead of degrading silently.
    * ``elastic`` — may be truncated, but keeps its floor while budget allows.
    * ``droppable`` — the first thing to give up its allocation.
``truncation``
    ``none`` (must fit whole), ``tail`` (keep the head), ``head`` (keep the
    tail), ``head_tail`` (keep both ends with a visible omission marker), or
    ``items`` (content is a list; drop whole entries instead of cutting text).
``count_by``
    ``tokens`` for text blocks; ``items`` for structured lists that must not be
    cut in half.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PROTECTION_HARD = "hard"
PROTECTION_ELASTIC = "elastic"
PROTECTION_DROPPABLE = "droppable"

TRUNCATION_NONE = "none"
TRUNCATION_TAIL = "tail"
TRUNCATION_HEAD = "head"
TRUNCATION_HEAD_TAIL = "head_tail"
TRUNCATION_ITEMS = "items"

Protection = Literal["hard", "elastic", "droppable"]
Truncation = Literal["none", "tail", "head", "head_tail", "items"]
CountBy = Literal["tokens", "items"]


@dataclass(frozen=True)
class ContextPolicy:
    kind: str
    priority: int
    weight: float
    min_tokens: int
    max_tokens: int
    protection: Protection
    truncation: Truncation
    count_by: CountBy = "tokens"

    @property
    def is_hard(self) -> bool:
        return self.protection == PROTECTION_HARD

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "priority": self.priority,
            "weight": self.weight,
            "min_tokens": self.min_tokens,
            "max_tokens": self.max_tokens,
            "protection": self.protection,
            "truncation": self.truncation,
            "count_by": self.count_by,
        }


#: Weights are carried over from the previous ``BLOCK_WEIGHTS`` so budgeting
#: behaviour stays comparable while the algorithm behind it changes.
CONTEXT_POLICIES: dict[str, ContextPolicy] = {
    "system_policy": ContextPolicy(
        kind="system_policy",
        priority=100,
        weight=0.10,
        # A hard block is never truncated, so `min_tokens` is a *floor the content
        # itself must clear* -- the shortest policy statement we accept. Keep it at
        # or below the shortest statement we actually ship, or every request fails.
        min_tokens=40,
        # A hard block is never truncated, so this ceiling must be able to hold the
        # whole rendered statement; it exists to catch a runaway statement, not to
        # cut one.
        max_tokens=4_000,
        protection=PROTECTION_HARD,
        truncation=TRUNCATION_NONE,
    ),
    "tenant_profile": ContextPolicy(
        kind="tenant_profile",
        priority=90,
        weight=0.03,
        min_tokens=30,
        max_tokens=800,
        protection=PROTECTION_HARD,
        truncation=TRUNCATION_NONE,
    ),
    "user_profile": ContextPolicy(
        kind="user_profile",
        priority=80,
        weight=0.05,
        min_tokens=40,
        max_tokens=1_200,
        protection=PROTECTION_ELASTIC,
        truncation=TRUNCATION_TAIL,
    ),
    "conversation_summary": ContextPolicy(
        kind="conversation_summary",
        priority=70,
        weight=0.12,
        min_tokens=0,
        max_tokens=1_600,
        protection=PROTECTION_ELASTIC,
        truncation=TRUNCATION_TAIL,
    ),
    "approval_state": ContextPolicy(
        kind="approval_state",
        priority=60,
        weight=0.08,
        min_tokens=20,
        # Whole or fail, like the other hard blocks: an approval conclusion cut in
        # half is worse than an explicit error.
        max_tokens=1_200,
        protection=PROTECTION_HARD,
        truncation=TRUNCATION_NONE,
    ),
    "task_slots": ContextPolicy(
        kind="task_slots",
        priority=55,
        weight=0.08,
        min_tokens=0,
        max_tokens=600,
        protection=PROTECTION_ELASTIC,
        truncation=TRUNCATION_TAIL,
    ),
    "retrieval_evidence": ContextPolicy(
        kind="retrieval_evidence",
        priority=50,
        weight=0.16,
        min_tokens=0,
        max_tokens=12_000,
        protection=PROTECTION_DROPPABLE,
        truncation=TRUNCATION_ITEMS,
        count_by="items",
    ),
    "tool_results": ContextPolicy(
        kind="tool_results",
        priority=45,
        weight=0.14,
        min_tokens=0,
        max_tokens=6_000,
        protection=PROTECTION_DROPPABLE,
        truncation=TRUNCATION_ITEMS,
        count_by="items",
    ),
    "recent_messages": ContextPolicy(
        kind="recent_messages",
        priority=95,
        weight=0.24,
        min_tokens=0,
        max_tokens=200_000,
        protection=PROTECTION_ELASTIC,
        truncation=TRUNCATION_HEAD_TAIL,
    ),
}

#: Unknown kinds get a conservative, droppable policy rather than a generous one:
#: a block the engine does not understand must not be able to squeeze out a block
#: it does.
UNKNOWN_POLICY = ContextPolicy(
    kind="unknown",
    priority=10,
    weight=0.05,
    min_tokens=0,
    max_tokens=2_000,
    protection=PROTECTION_DROPPABLE,
    truncation=TRUNCATION_TAIL,
)

#: Messages described by ``recent_messages`` still need to be reachable by their
#: own kind in reports and tests.
MESSAGES_KIND = "recent_messages"

#: Kinds that are rendered as a section inside the system prompt. Messages are
#: rendered as model messages instead, so they are excluded here.
RENDERED_KINDS = frozenset(CONTEXT_POLICIES) - {MESSAGES_KIND}


def policy_for(kind: str) -> ContextPolicy:
    policy = CONTEXT_POLICIES.get(kind)
    if policy is not None:
        return policy
    return UNKNOWN_POLICY


def total_weight(kinds: list[str]) -> float:
    return sum(policy_for(kind).weight for kind in kinds)


def describe_policies() -> dict[str, object]:
    """Reporting view for ``/ready``."""
    return {
        kind: policy.to_dict() for kind, policy in sorted(CONTEXT_POLICIES.items())
    }
