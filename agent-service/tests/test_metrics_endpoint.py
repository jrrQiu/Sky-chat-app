"""Metric-endpoint tests: valid Prometheus text, and nothing sensitive in it.

The endpoint reveals this deployment's models, traffic and spend, so it is
behind the internal caller identity like the rest of the operational surface.
Both properties are pinned here.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.observability.registry import MetricsRegistry
from app.observability.tokens import record_provider_usage
from tests.internal_auth import auth_headers

#: A value that must never appear in the exposition.
SECRET = "S3CRET-PROMPT-DO-NOT-LEAK"

_METRIC_LINE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?\d")


@pytest.fixture
def prometheus_client(monkeypatch):
    """A live app whose registry this test controls.

    Every module binds the registry at import time, so the patch has to cover
    each writer as well as the route that renders it.
    """
    local = MetricsRegistry()
    monkeypatch.setattr("app.api.routes.metrics.metrics", local)
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr("app.observability.tracing.metrics", local)

    with TestClient(create_app()) as test_client:
        yield test_client, local


def _seed(local: MetricsRegistry) -> None:
    # Real accounting, including a model with no verifiable price.
    record_provider_usage(
        "deepseek-flash",
        {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "prompt_tokens_details": {"cached_tokens": 1024},
        },
        latency_ms=42.0,
    )
    record_provider_usage(
        "deepseek-chat", {"prompt_tokens": 10, "completion_tokens": 5}, latency_ms=7.0
    )


def test_metrics_requires_the_internal_caller_identity(prometheus_client):
    client, _ = prometheus_client
    response = client.get("/metrics")
    assert response.status_code == 401


def test_metrics_renders_prometheus_text(prometheus_client):
    client, local = prometheus_client
    _seed(local)

    response = client.get("/metrics", headers=auth_headers("u1"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text

    # Every metric family is announced properly.
    assert "# HELP llm_calls" in body
    assert "# TYPE llm_calls counter" in body
    assert "# TYPE llm_latency_ms histogram" in body
    assert 'llm_tokens{kind="' in body or "llm_tokens{kind=" in body
    assert "llm_tokens{kind=input,model=deepseek-flash} 1200" in body
    assert "llm_cost_usd{model=deepseek-flash,rate=off-peak}" in body
    assert "llm_cost_unavailable{model=deepseek-chat," in body
    assert 'llm_latency_ms_bucket{model=deepseek-flash,outcome=ok,le="+Inf"} 1' in body
    assert "llm_latency_ms_count{model=deepseek-flash,outcome=ok} 1" in body


def test_metrics_text_parses_as_an_exposition(prometheus_client):
    """Every non-comment line must be `name{labels} value` or `name value`."""
    client, local = prometheus_client
    _seed(local)
    body = client.get("/metrics", headers=auth_headers("u1")).text

    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        assert _METRIC_LINE.match(line), f"not valid exposition syntax: {line!r}"

    # Histogram buckets must be cumulative and end at the recorded count.
    buckets = [
        int(match)
        for match in re.findall(
            r'llm_latency_ms_bucket\{model=deepseek-flash,outcome=ok,le="[^"]+"\} (\d+)',
            body,
        )
    ]
    assert buckets == sorted(buckets)
    assert buckets[-1] == 1


def test_metrics_leaks_no_content_or_identity(prometheus_client):
    """Only counts, timings, token totals and model names may appear."""
    client, local = prometheus_client
    _seed(local)

    # A caller cannot get content into the registry through the trace API: the
    # forbidden keys are dropped before anything is recorded or exported.
    from app.observability.tracing import trace_event, trace_model_call

    trace_event("user.question", {"prompt": SECRET, "user_id": SECRET})
    trace_model_call("deepseek-flash", 5, 5, 1)

    body = client.get("/metrics", headers=auth_headers("u1")).text

    assert SECRET not in body
    assert "prompt" not in body
    assert "user_id" not in body


def test_observability_status_route_reports_state(prometheus_client):
    client, _ = prometheus_client
    response = client.get("/v1/observability/status", headers=auth_headers("u1"))

    assert response.status_code == 200
    body = response.json()
    assert body["registry"]["active"] is True
    assert body["registry"]["prometheus_endpoint"] == "/metrics"
    assert body["otlp"]["auto_instrumentation"] is False
    assert body["langfuse"]["active"] is False
    assert body["cost"]["unpriced_behaviour"].startswith("cost reported as unavailable")


def test_observability_status_requires_the_internal_caller_identity(prometheus_client):
    client, _ = prometheus_client
    assert client.get("/v1/observability/status").status_code == 401


def test_metrics_is_empty_but_valid_before_any_traffic(prometheus_client):
    client, _ = prometheus_client
    response = client.get("/metrics", headers=auth_headers("u1"))
    assert response.status_code == 200
    assert response.text.strip() == ""
