"""Observability: in-process metrics, token/cost accounting and best-effort OTLP.

Import surface for call sites::

    from app.observability import metrics, trace_event, trace_model_call

``metrics`` is the single source of truth for what this process has done. The
Prometheus exposition lives in ``app.api.routes.metrics``; the pricing table and
its "unavailable rather than invented" contract live in ``app.observability.cost``.
"""

from app.observability.cost import (
    CostEstimate,
    ModelPrice,
    PriceTable,
    estimate_cost,
    load_price_table,
    price_table,
)
from app.observability.registry import MetricsRegistry, metrics
from app.observability.tokens import (
    ModelCallRecord,
    Usage,
    record_model_call,
    record_provider_usage,
)
from app.observability.tracing import (
    observability_status,
    trace_event,
    trace_model_call,
    trace_token_usage,
)

__all__ = [
    "CostEstimate",
    "MetricsRegistry",
    "ModelCallRecord",
    "ModelPrice",
    "PriceTable",
    "Usage",
    "estimate_cost",
    "load_price_table",
    "metrics",
    "observability_status",
    "price_table",
    "record_model_call",
    "record_provider_usage",
    "trace_event",
    "trace_model_call",
    "trace_token_usage",
]
