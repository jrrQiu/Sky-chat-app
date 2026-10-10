"""Token counting for the context budget.

The budget is a hard ceiling, so the number it is checked against has to be the
number the provider will actually charge for. The previous implementation used
``len(text) / 3.2`` and therefore **under-counted Chinese by roughly 2x**: a 8192
budget could send 15k+ tokens and get a provider 400. This module keeps a real
tokenizer as the first choice and states plainly which counter is in use.

Two implementations:

``TiktokenCounter``
    Preferred. ``cl100k_base`` is the closest widely-available BPE to the
    DeepSeek tokenizer; it is an approximation, which is why the engine also
    calibrates against the provider-reported ``usage`` (see ``report.py``).
``WeightedHeuristicCounter``
    Fallback when ``tiktoken`` is not installed or its BPE files are not
    reachable (a network-isolated image without a warm cache). Weighted per
    script instead of per character: CJK ~1.7 characters/token, Latin ~4
    characters/token, and digits/punctuation counted separately because they are
    token-dense in every BPE.

The counter is reported by name and version, so "estimated with a heuristic" can
never be mistaken for "counted with the model's tokenizer".
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger(__name__)

#: tiktoken encoding used as the DeepSeek approximation.
TIKTOKEN_ENCODING = "cl100k_base"

#: Texts longer than this are counted without caching, to bound memory. A 40k
#: character prompt is ~15k tokens; caching a handful of those is fine, caching
#: every paste in a long-lived process is not.
_MAX_CACHED_TEXT_CHARS = 64_000

_CJK = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)
_LATIN_WORD = re.compile(r"[A-Za-z]+")
_DIGITS = re.compile(r"[0-9]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)

#: Characters per token for CJK text. Chinese averages a little over one
#: character per token; 1.7 is the measured ratio for cl100k on prose.
_CJK_CHARS_PER_TOKEN = 1.7
#: Characters per token for Latin words (an English word is ~1.3 tokens).
_LATIN_CHARS_PER_TOKEN = 4.0
#: Single-character tokens: each digit and each punctuation mark is usually its
#: own token. Counting them separately is what stops "12345678" from being read
#: as two tokens.
_CHARS_PER_TOKEN_FOR_SYMBOLS = 1.0
#: Every message costs a few tokens of role/format scaffolding even when empty.
_PER_MESSAGE_OVERHEAD = 4


class TokenCounter(Protocol):
    """Counts tokens for one model."""

    name: str

    def count(self, text: str) -> int: ...

    def describe(self) -> dict[str, object]: ...


class TiktokenCounter:
    """Real BPE tokenizer (``tiktoken``)."""

    def __init__(self, encoding_name: str = TIKTOKEN_ENCODING) -> None:
        self._encoding_name = encoding_name
        import tiktoken  # noqa: PLC0415 - optional dependency, imported on demand

        self._encoding = tiktoken.get_encoding(encoding_name)
        self._version = getattr(tiktoken, "__version__", "unknown")
        self.name = f"tiktoken/{encoding_name}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        # `disallowed_special=()` so ordinary text containing a marker such as
        # `<|endoftext|>` is counted instead of raising.
        return len(self._encoding.encode(text, disallowed_special=()))

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": "bpe",
            "encoding": self._encoding_name,
            "version": self._version,
            "exact": False,
            "note": "cl100k_base approximates the DeepSeek tokenizer",
        }


class WeightedHeuristicCounter:
    """Script-weighted fallback. Deterministic, offline, and clearly not exact."""

    name = "heuristic/weighted-v2"

    def __init__(self, reason: str = "") -> None:
        self._reason = reason or "tiktoken unavailable"

    def count(self, text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK.findall(text))
        latin_chars = sum(len(match.group(0)) for match in _LATIN_WORD.finditer(text))
        digits = len(_DIGITS.findall(text))
        punctuation = len(_PUNCT.findall(text))
        # Whitespace between words is folded into the Latin word cost, so it is
        # not counted again here.
        tokens = (
            cjk / _CJK_CHARS_PER_TOKEN
            + latin_chars / _LATIN_CHARS_PER_TOKEN
            + digits / _CHARS_PER_TOKEN_FOR_SYMBOLS
            + punctuation / _CHARS_PER_TOKEN_FOR_SYMBOLS
        )
        return max(1, int(tokens + 0.999))

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": "heuristic",
            "exact": False,
            "cjk_chars_per_token": _CJK_CHARS_PER_TOKEN,
            "latin_chars_per_token": _LATIN_CHARS_PER_TOKEN,
            "reason": self._reason,
        }


def _build_counter(model: str | None) -> TokenCounter:
    try:
        counter = TiktokenCounter()
        logger.info("Context tokenizer: %s", counter.name)
        return counter
    except Exception as exc:  # pragma: no cover - depends on the image
        # Not fatal: the heuristic keeps the service serving. It is reported, so
        # an operator can see the budget is approximate.
        logger.warning(
            "tiktoken unavailable (%s); falling back to %s. Token budgets are "
            "approximate and calibrated against provider usage.",
            exc,
            WeightedHeuristicCounter.name,
        )
        return WeightedHeuristicCounter(reason=str(exc))


#: One counter per process: the encoding object is expensive to build and is
#: stateless for counting. A single model-agnostic counter is correct because the
#: heuristic is script-based and tiktoken's encoding does not depend on the model.
_counter: TokenCounter | None = None


def get_counter(model: str | None = None) -> TokenCounter:
    """Process-wide counter. ``model`` selects the encoding later if needed."""
    global _counter
    if _counter is None:
        _counter = _build_counter(model)
    return _counter


def describe_counter(model: str | None = None) -> dict[str, object]:
    return get_counter(model).describe()


def set_counter_for_testing(counter: TokenCounter | None) -> None:
    """Inject a counter (tests) or reset to lazy construction (``None``)."""
    global _counter
    _counter = counter


@lru_cache(maxsize=512)
def _count_cached(text: str) -> int:
    return get_counter().count(text)


def count_text(text: str) -> int:
    """Count tokens, caching the result for texts of a bounded size."""
    if not text:
        return 0
    if len(text) > _MAX_CACHED_TEXT_CHARS:
        return get_counter().count(text)
    return _count_cached(text)


def count_message(message: dict[str, object]) -> int:
    """Tokens for one chat message, including its role/format overhead."""
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    return count_text(text) + _PER_MESSAGE_OVERHEAD


def count_messages(messages: list[dict[str, object]]) -> int:
    return sum(count_message(message) for message in messages)
