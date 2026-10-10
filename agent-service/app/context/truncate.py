"""Budget-aware text cutting.

Every cut made here is *visible*: the omission marker states how much was left
out, so a reader (and the model) can tell a complete document from an excerpt.
The previous implementation cut on a character estimate and appended
``...[truncated]`` with no indication of the scale, which made a pathologically
truncated policy document look complete.

``head_tail`` exists for the one case where dropping the whole item is worse than
an excerpt: the current user message. A long paste keeps its opening (what is
being asked) and its closing (what is being asked *for*), which is where the ask
usually is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.context.policy import (
    TRUNCATION_HEAD,
    TRUNCATION_HEAD_TAIL,
    TRUNCATION_NONE,
    TRUNCATION_TAIL,
)
from app.context.tokenizer import count_text

Strategy = Literal["none", "tail", "head", "head_tail"]


@dataclass
class TruncationResult:
    """The text to use, plus what it cost to fit."""

    text: str
    tokens: int
    truncated: bool
    omitted_tokens: int = 0
    omitted_chars: int = 0
    strategy: Strategy = "none"
    notes: list[str] = field(default_factory=list)


def _marker(omitted_chars: int) -> str:
    return f"\n…[已省略 {max(0, omitted_chars)} 字]…\n"


def _count(text: str) -> int:
    return count_text(text)


def _fit_prefix(text: str, budget: int) -> str:
    """Longest prefix of ``text`` within ``budget`` tokens."""
    if budget <= 0 or not text:
        return ""
    if _count(text) <= budget:
        return text
    low, high = 0, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if _count(text[:middle]) <= budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return text[:best]


def _fit_suffix(text: str, budget: int) -> str:
    """Longest suffix of ``text`` within ``budget`` tokens."""
    if budget <= 0 or not text:
        return ""
    if _count(text) <= budget:
        return text
    low, high = 0, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if _count(text[len(text) - middle :]) <= budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return text[len(text) - best :] if best else ""


def truncate_text(
    text: str,
    budget_tokens: int,
    strategy: Strategy = TRUNCATION_TAIL,
) -> TruncationResult:
    """Fit ``text`` into ``budget_tokens`` using ``strategy``.

    ``none`` never cuts: when the text does not fit it is returned unchanged and
    the caller decides (a ``hard`` block fails; a droppable block is dropped).
    """
    if not text:
        return TruncationResult(text="", tokens=0, truncated=False, strategy=strategy)

    total = _count(text)
    if total <= budget_tokens:
        return TruncationResult(
            text=text, tokens=total, truncated=False, strategy=strategy
        )

    if strategy == TRUNCATION_NONE:
        return TruncationResult(
            text=text,
            tokens=total,
            truncated=False,
            strategy="none",
            notes=["strategy_none"],
        )

    if budget_tokens <= 0:
        return TruncationResult(
            text="",
            tokens=0,
            truncated=True,
            omitted_tokens=total,
            omitted_chars=len(text),
            strategy=strategy,
            notes=["no_budget"],
        )

    marker = _marker(len(text))
    marker_tokens = _count(marker)
    if marker_tokens >= budget_tokens:
        # Not even the marker fits inside the budget, so there is no honest way to
        # show an excerpt. Drop the whole text; the caller records it as omitted.
        return TruncationResult(
            text="",
            tokens=0,
            truncated=True,
            omitted_tokens=total,
            omitted_chars=len(text),
            strategy=strategy,
            notes=["marker_did_not_fit"],
        )

    room = budget_tokens - marker_tokens
    if strategy == TRUNCATION_HEAD:
        kept = _fit_suffix(text, room)
        rendered = marker + kept
    elif strategy == TRUNCATION_HEAD_TAIL:
        head = _fit_prefix(text, max(1, room // 2))
        tail = _fit_suffix(text, max(1, room - _count(head)))
        kept = head + tail
        rendered = head + marker + tail
    else:
        kept = _fit_prefix(text, room)
        rendered = kept + marker

    # The marker above sizes itself from the *whole* text; re-render once with the
    # real omission count, then trim the head if the longer marker pushed us over.
    true_marker = _marker(len(text) - len(kept))
    marker_tokens = _count(true_marker)
    if marker_tokens >= budget_tokens:
        return TruncationResult(
            text="",
            tokens=0,
            truncated=True,
            omitted_tokens=total,
            omitted_chars=len(text),
            strategy=strategy,
            notes=["marker_did_not_fit"],
        )
    room = budget_tokens - marker_tokens
    if strategy == TRUNCATION_HEAD:
        kept = _fit_suffix(text, room)
        rendered = true_marker + kept
    elif strategy == TRUNCATION_HEAD_TAIL:
        head = _fit_prefix(text, max(1, room // 2))
        tail = _fit_suffix(text, max(1, room - _count(head)))
        kept = head + tail
        rendered = head + true_marker + tail
    else:
        kept = _fit_prefix(text, room)
        rendered = kept + true_marker

    rendered_tokens = _count(rendered)
    if rendered_tokens > budget_tokens:
        # Last resort: shave the head token by token. Keeps the hard ceiling even
        # when the tokenizer disagrees with the arithmetic above.
        head = _fit_prefix(kept, max(1, budget_tokens - marker_tokens))
        rendered = head + true_marker
        rendered_tokens = _count(rendered)
        kept = head
    return TruncationResult(
        text=rendered,
        tokens=rendered_tokens,
        truncated=True,
        omitted_tokens=max(0, total - _count(kept)),
        omitted_chars=max(0, len(text) - len(kept)),
        strategy=strategy,
    )
