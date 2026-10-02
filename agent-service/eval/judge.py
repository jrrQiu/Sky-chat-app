"""An optional LLM judge for groundedness.

Groundedness asks a question no formula can answer: does the evidence retrieved
for this query actually support answering it? The only honest implementations
are therefore "ask a model" or "report unavailable". This module supplies the
first, and `eval.metrics` falls back to the second when no judge is passed.

Determinism for CI:

* **temperature 0** is sent explicitly (the provider accepts it),
* the judge prompt is versioned (`PROMPT_VERSION`) and recorded in the report,
* the response is constrained to a small JSON object, and a response that cannot
  be parsed is reported as "could not judge" rather than coerced to a number.

Cost is not hidden: every judge call is recorded through the metrics registry
(``llm.calls``, ``llm.latency_ms``, ``llm.tokens``, ``llm.cost_usd``) by reusing
`LLMStructuredClient`, so a judge that runs up a bill is visible in `/metrics`.

The judge is a real model call, so it costs money and can fail. It is never
enabled by default — `run_eval.py --judge` turns it on.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from app.config import settings
from app.retrieval.llm import LLMStructuredClient

logger = logging.getLogger(__name__)

#: Bump whenever `SYSTEM_PROMPT` or the scoring rule changes, so a stored score
#: can be traced back to the prompt that produced it.
PROMPT_VERSION = "groundedness-judge/v1"

SYSTEM_PROMPT = (
    "You are a strict retrieval-quality judge. You receive a user query and the "
    "evidence items retrieved for it. Score each evidence item on whether it "
    "genuinely supports answering the query: 1.0 = directly supports it, "
    "0.5 = related but insufficient, 0.0 = unrelated. Ignore writing style and "
    "length. Do not reward keywords alone. Respond with JSON only, no prose: "
    '{"scores": [<one number per evidence item, in order>], "reason": "<short reason>"}'
)

#: Bounded so one judge call cannot run away on a large evidence set.
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 1200


class LLMGroundednessJudge:
    """Callable judge backed by the structured-completion LLM client."""

    def __init__(self, client: LLMStructuredClient | None = None) -> None:
        self._client = client or LLMStructuredClient()
        #: Recorded in the evaluation report next to the score.
        self.prompt_version = PROMPT_VERSION
        self.model = getattr(self._client, "model", settings.deepseek_model)

    @property
    def available(self) -> bool:
        """False when no API key is configured, so no call will be attempted."""
        return bool(getattr(self._client, "api_key", ""))

    def build_user_prompt(
        self,
        query: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> str:
        items = list(evidence)[:MAX_EVIDENCE_ITEMS]
        lines = [f"QUERY: {query}", "", f"EVIDENCE ({len(items)} items):"]
        for index, item in enumerate(items, start=1):
            content = str(item.get("content") or "")[:MAX_EVIDENCE_CHARS]
            lines.append(f"[{index}] title: {item.get('title') or item.get('source_id')}")
            lines.append(f"[{index}] text: {content}")
        return "\n".join(lines)

    def __call__(
        self,
        query: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> float | None:
        """Return a score in [0, 1], or `None` when the judge cannot score."""
        if not evidence:
            # Nothing to judge. The report renders this as unavailable, not as 1.
            return None
        if not self.available:
            logger.warning("Groundedness judge has no API key; reporting unavailable")
            return None

        payload = self._client.complete_json(
            SYSTEM_PROMPT, self.build_user_prompt(query, evidence)
        )
        if not payload:
            return None
        return self.score_from_payload(payload, len(evidence))

    @staticmethod
    def score_from_payload(payload: Mapping[str, Any], item_count: int) -> float | None:
        """Validate and average a judge payload. Returns `None` if unusable."""
        raw_scores = payload.get("scores")
        if isinstance(raw_scores, str):
            try:
                raw_scores = json.loads(raw_scores)
            except json.JSONDecodeError:
                return None
        if not isinstance(raw_scores, list) or not raw_scores:
            return None

        values: list[float] = []
        for raw in raw_scores:
            if isinstance(raw, bool):
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if not 0.0 <= value <= 1.0:
                return None
            values.append(value)
        if not values:
            return None
        # A judge that scored fewer items than it was shown is not trusted to be
        # complete; report unavailable rather than average a partial answer.
        if item_count and len(values) != min(item_count, MAX_EVIDENCE_ITEMS):
            return None
        return sum(values) / len(values)


def build_llm_judge(
    client: LLMStructuredClient | None = None,
) -> LLMGroundednessJudge | None:
    """Build a judge when a model is callable, else `None`.

    Returning `None` is the signal that makes `run_eval` treat groundedness as
    unavailable and (by default) fail the gate, instead of asserting 1.0.
    """
    judge = LLMGroundednessJudge(client)
    if not judge.available:
        return None
    return judge
