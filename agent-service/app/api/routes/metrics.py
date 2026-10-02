"""Prometheus exposition and the observability status route.

Two endpoints, both gated behind the service's internal auth:

``GET /metrics``
    Prometheus text format (version 0.0.4). Counters, timers, token totals, model
    names and cost estimates only.

``GET /v1/observability/status``
    JSON describing what instrumentation is actually active. This exists because
    ``/ready`` is owned by concurrent work on a different module; publishing the
    state here keeps this change additive.

**Why the auth gate.** ``/health`` and ``/ready`` are intentionally
unauthenticated probe endpoints, but everything else in this service requires a
signed internal caller identity. ``/metrics`` reveals this deployment's models,
traffic volume and spend, which is useful reconnaissance, so it is treated as an
internal operational endpoint (``require_internal_token``) rather than a probe.
A scraper authenticates the same way the Java service does. The consequence,
stated plainly: a plain Prometheus scrape config cannot read this endpoint
without a token.

**What cannot leak.** The registry has no API for free-form labels, so there is
no path for a prompt, message body, user id or secret to reach the exposition.
The only label values written anywhere in this codebase are model names, token
kinds, outcomes, an OTLP failure reason and a price-rate basis. The tests assert
this against a payload seeded with obvious secret material.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.core.security import Caller, require_caller
from app.observability.registry import metrics
from app.observability.tracing import observability_status

router = APIRouter(tags=["observability"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=True)
async def prometheus_metrics(
    caller: Caller = Depends(require_caller),
) -> Response:
    """Render the registry in the Prometheus text exposition format."""
    body = metrics.render_prometheus()
    return Response(
        content=body,
        media_type=PROMETHEUS_CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/v1/observability/status")
async def observability_state(
    caller: Caller = Depends(require_caller),
) -> dict[str, object]:
    """Report which instrumentation is active, and which is not."""
    return observability_status()
