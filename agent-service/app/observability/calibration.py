"""Online calibration of the context budget against the provider's own count.

The budget is only as good as the tokenizer behind it. `cl100k_base` is an
*approximation* of the DeepSeek tokenizer, so a systematic gap is possible — and
it is exactly the kind of gap that shows up as "the prompt was rejected for being
too long" only in production, at the largest inputs.

This module closes that loop. After every completion the provider reports
``prompt_tokens``; comparing it with what the engine measured gives a relative
error. That error is:

* recorded as a metric, so a trend is visible on ``/metrics``;
* smoothed with an exponential moving average per model, so ``/ready`` can state
  the current bias rather than a single noisy sample;
* turned into a count of samples past the alert threshold, which is the signal
  that the vendor changed tokenizers and the counter needs attention.

No prompt text and no user id is ever recorded here: the label set is the model
name only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from app.context.report import CALIBRATION_ALERT_THRESHOLD, estimate_error_ratio
from app.observability.registry import metrics

logger = logging.getLogger(__name__)

METRIC_CALIBRATION = "context.estimate_error_ratio"
METRIC_CALIBRATION_SAMPLES = "context.calibration_samples"
METRIC_BUDGET_INFEASIBLE = "context.budget_infeasible"
METRIC_UTILIZATION = "context.utilization"
METRIC_BLOCKS = "context.blocks"
METRIC_DROPPED_MESSAGES = "context.dropped_messages"
METRIC_EVIDENCE_DROPPED = "context.evidence_dropped"
METRIC_NEEDS_SUMMARY = "context.needs_summary"
METRIC_CONTEXT_TOKENS = "context.used_tokens"

HELP_CALIBRATION = (
    "Smoothed relative error between the engine's token estimate and the "
    "provider-reported input tokens, per model."
)
HELP_CALIBRATION_SAMPLES = (
    "Samples whose estimation error exceeded the alert threshold, per model."
)
HELP_BUDGET_INFEASIBLE = (
    "Context assemblies that could not satisfy a guarantee and refused, per window."
)
HELP_UTILIZATION = "Share of the usable context budget consumed, per model."
HELP_BLOCKS = "Context blocks placed or truncated, per kind and outcome."
HELP_DROPPED_MESSAGES = "Messages evicted from the prompt, per reason."
HELP_EVIDENCE_DROPPED = "Evidence items dropped from the prompt by the budget."
HELP_NEEDS_SUMMARY = "Turns that required a summary revision, per model."
HELP_CONTEXT_TOKENS = "Tokens the context engine measured, per model and block kind."

#: Weight of the newest sample in the moving average. 0.2 keeps roughly the last
#: ten calls relevant, which is long enough to be stable and short enough to react.
EMA_ALPHA = 0.2


@dataclass
class CalibrationState:
    samples: int = 0
    alerts: int = 0
    ema: float | None = None
    last_ratio: float | None = None
    last_estimated: int = 0
    last_provider: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "alerts": self.alerts,
            "ema_error_ratio": None if self.ema is None else round(self.ema, 6),
            "last_error_ratio": (
                None if self.last_ratio is None else round(self.last_ratio, 6)
            ),
            "last_estimated_input_tokens": self.last_estimated,
            "last_provider_input_tokens": self.last_provider,
            "alert_threshold": CALIBRATION_ALERT_THRESHOLD,
        }


class CalibrationTracker:
    """Per-model moving average of the estimation error."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, CalibrationState] = {}

    def record(
        self,
        model: str,
        *,
        estimated_input_tokens: int | None,
        provider_usage: Mapping[str, Any] | None,
    ) -> float | None:
        provider_input = None
        if isinstance(provider_usage, Mapping):
            raw = provider_usage.get("prompt_tokens", provider_usage.get("input_tokens"))
            try:
                provider_input = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                provider_input = None
        ratio = estimate_error_ratio(estimated_input_tokens, provider_input)
        if ratio is None:
            # No provider count means no measurement. Recorded as such rather than
            # as a perfect zero.
            return None

        label = (model or "unknown").strip() or "unknown"
        with self._lock:
            state = self._states.setdefault(label, CalibrationState())
            state.samples += 1
            state.last_ratio = ratio
            state.last_estimated = int(estimated_input_tokens or 0)
            state.last_provider = int(provider_input or 0)
            previous = state.ema
            state.ema = ratio if previous is None else (
                EMA_ALPHA * ratio + (1 - EMA_ALPHA) * previous
            )
            if abs(ratio) > CALIBRATION_ALERT_THRESHOLD:
                state.alerts += 1
            smoothed = state.ema
            alert = abs(ratio) > CALIBRATION_ALERT_THRESHOLD

        metrics.timer(
            METRIC_CALIBRATION, HELP_CALIBRATION, model=label
        ).observe(abs(smoothed or 0.0) * 1000.0)
        metrics.counter(
            METRIC_CALIBRATION_SAMPLES,
            HELP_CALIBRATION_SAMPLES,
            model=label,
            exceeded="true" if alert else "false",
        ).inc()
        return ratio

    def describe(self) -> dict[str, Any]:
        with self._lock:
            states = {name: state.describe() for name, state in self._states.items()}
        return {
            "models": states,
            "alert_threshold": CALIBRATION_ALERT_THRESHOLD,
            "note": (
                "error = (provider_input - estimated_input) / provider_input; "
                "positive means the engine under-counts"
            ),
        }

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


calibration = CalibrationTracker()


def record_context_report(report: Mapping[str, Any] | None, *, model: str = "") -> None:
    """Publish one context report to the metric registry.

    Called once per assembled prompt. Labels stay a closed set: model, block kind,
    outcome. No prompt text, no user id, no conversation id.
    """
    if not isinstance(report, Mapping):
        return
    label = (model or str(report.get("model") or "unknown")).strip() or "unknown"
    metrics.timer(METRIC_UTILIZATION, HELP_UTILIZATION, model=label).observe(
        float(report.get("utilization") or 0.0) * 1000.0
    )
    metrics.counter(
        METRIC_CONTEXT_TOKENS, HELP_CONTEXT_TOKENS, model=label, kind="total"
    ).inc(float(report.get("used_tokens") or 0))
    for block in report.get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        kind = str(block.get("kind") or "unknown")
        outcome = (
            "dropped"
            if block.get("dropped")
            else "truncated"
            if block.get("truncated")
            else "kept"
        )
        metrics.counter(
            METRIC_BLOCKS, HELP_BLOCKS, kind=kind, outcome=outcome
        ).inc()
        metrics.counter(
            METRIC_CONTEXT_TOKENS, HELP_CONTEXT_TOKENS, model=label, kind=kind
        ).inc(float(block.get("actual") or 0))
    messages = report.get("messages") or {}
    if isinstance(messages, Mapping):
        dropped = int(messages.get("dropped") or 0)
        if dropped:
            metrics.counter(
                METRIC_DROPPED_MESSAGES, HELP_DROPPED_MESSAGES, reason="budget"
            ).inc(dropped)
    evidence = report.get("evidence") or {}
    if isinstance(evidence, Mapping):
        dropped_evidence = int(evidence.get("dropped") or 0)
        if dropped_evidence:
            metrics.counter(METRIC_EVIDENCE_DROPPED, HELP_EVIDENCE_DROPPED).inc(
                dropped_evidence
            )
    if report.get("needs_summary"):
        metrics.counter(METRIC_NEEDS_SUMMARY, HELP_NEEDS_SUMMARY, model=label).inc()


def record_budget_infeasible(*, window: int, missing_tokens: int = 0) -> None:
    """Count a refused assembly. The alert is `context_budget_infeasible > 0`.

    An infeasible assembly is a misconfiguration (or a bug) rather than a normal
    outcome, so it is counted by window only: the operator needs to know it
    happened, and the log line carries the shortfall.
    """
    metrics.counter(
        METRIC_BUDGET_INFEASIBLE, HELP_BUDGET_INFEASIBLE, window=str(window)
    ).inc()
    if missing_tokens:
        logger.warning(
            "Context budget infeasible: window=%s missing_tokens=%s",
            window,
            missing_tokens,
        )


def describe_calibration() -> dict[str, Any]:
    return calibration.describe()
