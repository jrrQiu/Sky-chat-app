"""Trace entry points for the agent service.

These two functions used to be conditional ``return`` statements with comments
claiming that OpenTelemetry and Langfuse instrumentation happened "here". The
comments were not true: nothing was instrumented, so the service had no traces,
no token accounting and no cost data — which is exactly why the evaluation and
cost work in this repository could not be grounded.

What is true now, and what is not:

* **True.** Every ``trace_event`` increments a counter and every
  ``trace_model_call`` records tokens, latency and cost into the in-process
  registry, which ``GET /metrics`` exposes. This works with no configuration at
  all, in tests and in production.
* **True, best-effort.** When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, a span is
  exported over OTLP/HTTP with the ``httpx`` dependency that already exists, with
  a short timeout, and a failure only increments a counter.
* **Not implemented.** The OpenTelemetry *SDK* and the Langfuse SDK are not used
  by this module. There is no FastAPI or LiteLLM auto-instrumentation and no
  Langfuse export. Adding either would mean new dependencies, which this change
  is not allowed to take on. ``observability_status()`` reports
  ``langfuse: "configured-but-not-exporting"`` when keys are set, rather than
  letting a stale comment imply it works.

Both functions are safe to call from anywhere: they never raise, never block on
the network beyond the exporter timeout, and never record message content.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.config import settings
from app.observability import otlp
from app.observability.cost import price_table
from app.observability.registry import metrics
from app.observability.tokens import (
    ModelCallRecord,
    Usage,
    record_model_call as _record_model_call,
)

logger = logging.getLogger(__name__)

METRIC_EVENTS = "trace.events"
HELP_EVENTS = "Application-level trace events, by name."

#: Attribute keys that must never leave the process in a span.
_FORBIDDEN_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "message",
        "content",
        "answer",
        "question",
        "user_id",
        "userid",
        "authorization",
        "api_key",
        "secret",
        "password",
    }
)


def _safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only scalar, non-sensitive attributes.

    A span attribute is exported to a third party, so this is a filter and not a
    formality: message content and caller identity are dropped here rather than
    trusted to the caller not to pass them.
    """
    if not attributes:
        return {}
    safe: dict[str, Any] = {}
    for raw_key, value in attributes.items():
        key = str(raw_key)
        if key.strip().lower() in _FORBIDDEN_KEYS:
            continue
        if isinstance(value, (bool, int, float, str)):
            safe[key] = value
    return safe


def trace_event(
    name: str,
    attributes: dict[str, object] | None = None,
) -> None:
    """Record an application event. Never raises, never sends content.

    The counter is incremented for every event, with no label taken from the
    event name, because an unbounded label would let request data blow up the
    registry's cardinality. The name itself is carried on the OTLP span only.
    """
    try:
        metrics.counter(METRIC_EVENTS, HELP_EVENTS).inc()
    except Exception:  # noqa: BLE001 - telemetry must not break the caller
        logger.debug("trace_event could not record %s", name, exc_info=True)
        return

    if not otlp.otlp_endpoint():
        return
    try:
        otlp.export_span(name, _safe_attributes(attributes))
    except Exception:  # noqa: BLE001 - telemetry must not break the caller
        logger.debug("trace_event export failed for %s", name, exc_info=True)


def trace_model_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    *,
    cached_tokens: int | None = None,
    outcome: str = "ok",
) -> ModelCallRecord | None:
    """Record the token, latency and cost footprint of one model call.

    ``cached_tokens`` is keyword-only and optional so existing call sites keep
    working; when it is ``None`` the provider did not report a cache breakdown
    and cache-hit tokens are recorded as zero while the cost still reflects only
    the tokens that were actually reported.
    """
    try:
        record = _record_model_call(
            model,
            latency_ms=float(duration_ms),
            usage=Usage(
                input_tokens=max(0, int(input_tokens)),
                output_tokens=max(0, int(output_tokens)),
                cached_tokens=None if cached_tokens is None else max(0, int(cached_tokens)),
            ),
            outcome=outcome,
        )
    except Exception:  # noqa: BLE001 - telemetry must not break the caller
        logger.debug("trace_model_call could not record %s", model, exc_info=True)
        return None

    if otlp.otlp_endpoint():
        attributes: dict[str, Any] = {
            "gen_ai.system": "deepseek",
            "gen_ai.request.model": record.model,
            "gen_ai.usage.input_tokens": record.usage.input_tokens if record.usage else 0,
            "gen_ai.usage.output_tokens": record.usage.output_tokens if record.usage else 0,
            "duration_ms": duration_ms,
            "outcome": outcome,
        }
        if record.usage and record.usage.cached_tokens is not None:
            attributes["gen_ai.usage.cached_tokens"] = record.usage.cached_tokens
        if record.cost is not None and record.cost.available:
            attributes["gen_ai.usage.cost_usd"] = round(record.cost.cost_usd or 0.0, 8)
        try:
            otlp.export_span("llm.completion", attributes, duration_ms=float(duration_ms))
        except Exception:  # noqa: BLE001
            logger.debug("trace_model_call export failed for %s", model, exc_info=True)
    return record


def trace_token_usage(
    model: str,
    usage: Mapping[str, Any] | None,
    latency_ms: float,
    *,
    outcome: str = "ok",
) -> ModelCallRecord | None:
    """Record a provider ``usage`` payload verbatim (streaming included)."""
    try:
        return _record_model_call(
            model,
            latency_ms=latency_ms,
            usage=Usage.from_provider_payload(usage),
            outcome=outcome,
        )
    except Exception:  # noqa: BLE001 - telemetry must not break the caller
        logger.debug("trace_token_usage could not record %s", model, exc_info=True)
        return None


def observability_status() -> dict[str, Any]:
    """What is and is not active. Surfaced by ``/metrics`` and the status route.

    This exists so an operator can tell the difference between "no traces
    because there is no traffic" and "no traces because nothing is exporting".
    """
    otlp_state = otlp.exporter_state()
    langfuse_configured = bool(
        settings.langfuse_public_key and settings.langfuse_secret_key
    )
    return {
        "registry": {
            "active": True,
            "kind": "in-process",
            "scope": "per-replica",
            "prometheus_endpoint": "/metrics",
        },
        "otlp": otlp_state,
        "langfuse": {
            "configured": langfuse_configured,
            # The SDK is deliberately not a dependency of this module.
            "active": False,
            "state": (
                "configured-but-not-exporting" if langfuse_configured else "not-configured"
            ),
            "reason": "the Langfuse SDK is not used; model traces go to the registry and OTLP",
        },
        "cost": {
            "rate": price_table.rate,
            "priced_models": price_table.models,
            "unpriced_behaviour": "cost reported as unavailable, never estimated",
        },
    }
