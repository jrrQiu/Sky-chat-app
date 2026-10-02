"""Token, latency and cost accounting for every model call.

The provider is the only trustworthy source for token counts, so this module
does not estimate them: it records what the response actually reported, and
records the *absence* of a report explicitly. That distinction matters for the
P1 cost work, because "0 tokens" and "the provider did not tell us" must not
look the same in a report.

Metric vocabulary (all read back by ``/metrics`` and by the evaluation harness):

===================== ========= =========================================
name                  type      labels
===================== ========= =========================================
``llm.calls``         counter   ``model``, ``outcome``
``llm.latency_ms``    timer     ``model``, ``outcome``
``llm.tokens``        counter   ``model``, ``kind`` (input/output/cached)
``llm.cost_usd``      counter   ``model``, ``rate``
``llm.cost_unavailable`` counter   ``model``, ``reason``
``llm.cache_hit_ratio_observed`` counter ``model``, ``kind``
===================== ========= =========================================

``model`` is a model name, never a caller identity or a prompt. The label set is
closed by construction: `record_model_call` is the only writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.observability.cost import CostEstimate, estimate_cost
from app.observability.registry import metrics

METRIC_CALLS = "llm.calls"
METRIC_LATENCY = "llm.latency_ms"
METRIC_TOKENS = "llm.tokens"
METRIC_COST = "llm.cost_usd"
METRIC_COST_UNAVAILABLE = "llm.cost_unavailable"
METRIC_USAGE_REPORTED = "llm.usage_reporting"

HELP_CALLS = "Model calls made by this process, by model and outcome."
HELP_LATENCY = "Wall-clock duration of model calls in milliseconds."
HELP_TOKENS = "Tokens reported by the provider, by model and token kind."
HELP_COST = "Estimated model spend in USD from the configured price table."
HELP_COST_UNAVAILABLE = (
    "Model calls whose cost could not be computed because no verified price "
    "exists for the model or the provider did not report cache-hit tokens."
)
HELP_USAGE_REPORTED = (
    "Model calls that did or did not carry provider usage data, so an "
    "unreported token count is never mistaken for a zero."
)


@dataclass(frozen=True)
class Usage:
    """Provider-reported token usage for one call.

    ``cached_tokens`` is ``None`` when the provider did not report a cache
    breakdown at all, which is different from reporting zero cache hits.
    """

    input_tokens: int
    output_tokens: int
    cached_tokens: int | None = None

    @classmethod
    def from_provider_payload(cls, payload: Mapping[str, Any] | None) -> Usage | None:
        """Parse an OpenAI-compatible ``usage`` object, or return ``None``."""
        if not isinstance(payload, Mapping) or not payload:
            return None
        prompt_tokens = payload.get("prompt_tokens")
        completion_tokens = payload.get("completion_tokens")
        if prompt_tokens is None and completion_tokens is None:
            return None

        cached = _nested_int(payload, ("prompt_tokens_details", "cached_tokens"))
        if cached is None:
            cached = _nested_int(payload, ("prompt_cache_hit_tokens",))
        return cls(
            input_tokens=int(prompt_tokens or 0),
            output_tokens=int(completion_tokens or 0),
            cached_tokens=cached,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
        }


def _nested_int(payload: Mapping[str, Any], path: tuple[str, ...]) -> int | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ModelCallRecord:
    """One completed model call as recorded by the metrics registry."""

    model: str
    outcome: str
    latency_ms: float
    usage: Usage | None
    cost: CostEstimate | None


def record_model_call(
    model: str,
    *,
    latency_ms: float,
    usage: Usage | None,
    outcome: str = "ok",
    cost: CostEstimate | None = None,
) -> ModelCallRecord:
    """Record one model call. Safe to call from any thread.

    A cost of ``None`` is treated as "unavailable", never as zero spend.
    """
    model_label = (model or "unknown").strip() or "unknown"
    timer = metrics.timer(METRIC_LATENCY, HELP_LATENCY, model=model_label, outcome=outcome)
    timer.observe(max(0.0, float(latency_ms)))
    metrics.counter(METRIC_CALLS, HELP_CALLS, model=model_label, outcome=outcome).inc()

    if usage is None:
        metrics.counter(
            METRIC_USAGE_REPORTED,
            HELP_USAGE_REPORTED,
            model=model_label,
            reported="false",
        ).inc()
        return ModelCallRecord(
            model=model_label,
            outcome=outcome,
            latency_ms=latency_ms,
            usage=None,
            cost=None,
        )

    metrics.counter(
        METRIC_USAGE_REPORTED, HELP_USAGE_REPORTED, model=model_label, reported="true"
    ).inc()
    for kind, amount in (
        ("input", usage.input_tokens),
        ("output", usage.output_tokens),
        ("cached", usage.cached_tokens if usage.cached_tokens is not None else 0),
    ):
        metrics.counter(METRIC_TOKENS, HELP_TOKENS, model=model_label, kind=kind).inc(
            max(0, int(amount))
        )

    resolved_cost = cost or estimate_cost(
        model_label,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
    )
    if resolved_cost.available:
        metrics.counter(
            METRIC_COST,
            HELP_COST,
            model=model_label,
            rate=resolved_cost.price.rate_basis if resolved_cost.price else "unknown",
        ).inc(resolved_cost.cost_usd or 0.0)
    else:
        metrics.counter(
            METRIC_COST_UNAVAILABLE,
            HELP_COST_UNAVAILABLE,
            model=model_label,
            reason=resolved_cost.unavailable_reason or "unknown",
        ).inc()

    return ModelCallRecord(
        model=model_label,
        outcome=outcome,
        latency_ms=latency_ms,
        usage=usage,
        cost=resolved_cost,
    )


def record_provider_usage(
    model: str,
    payload: Mapping[str, Any] | None,
    *,
    latency_ms: float,
    outcome: str = "ok",
) -> ModelCallRecord:
    """Record a call from a raw provider ``usage`` payload."""
    return record_model_call(
        model,
        latency_ms=latency_ms,
        usage=Usage.from_provider_payload(payload),
        outcome=outcome,
    )
