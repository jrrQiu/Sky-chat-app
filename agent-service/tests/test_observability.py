"""Tests for the real observability layer.

These pin the three properties the P1 work depends on:

1. the registry counts what it is told to count, with labels,
2. provider usage is turned into token and cost totals without inventing a
   number for a model that has no verified price,
3. nothing sensitive can reach the exposition.
"""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from app.observability.cost import (
    DEFAULT_PRICES,
    OFF_PEAK,
    PEAK,
    ModelPrice,
    PriceTable,
    load_price_table,
)
from app.observability.registry import MetricsRegistry
from app.observability.tokens import (
    METRIC_CALLS,
    METRIC_COST,
    METRIC_COST_UNAVAILABLE,
    METRIC_LATENCY,
    METRIC_TOKENS,
    METRIC_USAGE_REPORTED,
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


@pytest.fixture
def registry() -> MetricsRegistry:
    """A private registry, so the process-wide one is never mutated by a test."""
    return MetricsRegistry()


# ------------------------------------------------------------ registry basics


def test_counter_increments_and_defaults_to_zero(registry):
    counter = registry.counter("jobs.done", "Jobs finished.", queue="a")
    assert counter.value == 0.0
    counter.inc()
    counter.inc(4)
    assert counter.value == 5.0


def test_counter_labels_are_partitioned(registry):
    registry.counter("jobs.done", "Jobs finished.", queue="a").inc(3)
    registry.counter("jobs.done", "Jobs finished.", queue="b").inc(1)

    assert registry.counter_value("jobs.done", queue="a") == 3.0
    assert registry.counter_value("jobs.done", queue="b") == 1.0
    # An unseen label set reads as zero rather than raising.
    assert registry.counter_value("jobs.done", queue="c") == 0.0
    assert registry.counter_value("never.declared", queue="a") == 0.0


def test_counter_label_order_does_not_matter(registry):
    registry.counter("calls", "Calls.", model="m", outcome="ok").inc(2)
    assert registry.counter_value("calls", outcome="ok", model="m") == 2.0


def test_counter_is_thread_safe(registry):
    """Two worker threads incrementing must not lose an update."""
    counter = registry.counter("concurrent", "Concurrent increments.")

    def worker():
        for _ in range(500):
            counter.inc()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.value == 2000.0


def test_timer_observes_count_sum_and_mean(registry):
    timer = registry.timer("op.latency_ms", "Latency.", kind="x")
    timer.observe(10.0)
    timer.observe(30.0)

    count, mean = registry.histogram_stats("op.latency_ms", kind="x")
    assert count == 2
    assert mean == 20.0


def test_timer_buckets_are_cumulative(registry):
    timer = registry.timer(
        "op.latency_ms", "Latency.", buckets_ms=(10.0, 100.0), kind="x"
    )
    timer.observe(5.0)  # <= 10
    timer.observe(50.0)  # <= 100
    timer.observe(5000.0)  # only +Inf

    rendered = registry.render_prometheus()
    assert 'op_latency_ms_bucket{kind=x,le="10"} 1' in rendered
    assert 'op_latency_ms_bucket{kind=x,le="100"} 2' in rendered
    assert 'op_latency_ms_bucket{kind=x,le="+Inf"} 3' in rendered
    assert "op_latency_ms_count{kind=x} 3" in rendered
    assert "op_latency_ms_sum{kind=x} 5055" in rendered


def test_timer_context_manager_records_a_duration(registry):
    timer = registry.timer("work.latency_ms", "Latency.")
    with timer.time():
        pass

    count, mean = registry.histogram_stats("work.latency_ms")
    assert count == 1
    assert mean >= 0.0


def test_render_emits_help_and_type_for_every_family(registry):
    registry.counter("a.count", "A counter.", label="1")
    registry.timer("b.latency_ms", "A timer.", label="1")

    rendered = registry.render_prometheus()
    assert "# HELP a_count A counter." in rendered
    assert "# TYPE a_count counter" in rendered
    assert "# HELP b_latency_ms A timer." in rendered
    assert "# TYPE b_latency_ms histogram" in rendered
    assert rendered.endswith("\n")


def test_metric_and_label_names_are_sanitized(registry):
    registry.counter("weird name/with.dashes", "Odd.", **{"odd label": 1}).inc()
    rendered = registry.render_prometheus()
    assert "weird_name_with_dashes{odd_label=1} 1" in rendered


def test_label_values_cannot_forge_a_metric_line(registry):
    registry.counter("evil", "Evil.", note='x" \n fake_metric 99').inc()
    rendered = registry.render_prometheus()

    body_lines = [line for line in rendered.splitlines() if not line.startswith("#")]
    assert len(body_lines) == 1, rendered
    assert "fake_metric" in body_lines[0]  # escaped inside the label, not a new line
    assert '\\n' in body_lines[0] and '\\"' in body_lines[0]


def test_reset_drops_every_family(registry):
    registry.counter("gone", "Gone.").inc()
    registry.reset()
    assert registry.counter_value("gone") == 0.0
    assert registry.render_prometheus() == "\n"


# ------------------------------------------------- token and cost accounting


def test_usage_parses_the_openai_compatible_shape():
    usage = Usage.from_provider_payload(
        {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "prompt_tokens_details": {"cached_tokens": 1024},
        }
    )
    assert usage is not None
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 300
    assert usage.cached_tokens == 1024


def test_usage_parses_the_deepseek_cache_hit_field():
    usage = Usage.from_provider_payload(
        {"prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 64}
    )
    assert usage is not None
    assert usage.cached_tokens == 64


def test_missing_usage_is_none_not_zero():
    assert Usage.from_provider_payload(None) is None
    assert Usage.from_provider_payload({}) is None
    assert Usage.from_provider_payload({"other": 1}) is None


def test_record_model_call_accounts_tokens_cost_and_latency(registry, monkeypatch):
    monkeypatch.setattr("app.observability.tokens.metrics", registry)

    record = record_provider_usage(
        "deepseek-flash",
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
        latency_ms=250.0,
    )

    assert registry.counter_value(METRIC_CALLS, model="deepseek-flash", outcome="ok") == 1.0
    assert registry.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="input") == 1_000_000
    assert registry.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="output") == 1_000_000
    count, mean = registry.histogram_stats(
        METRIC_LATENCY, model="deepseek-flash", outcome="ok"
    )
    assert count == 1 and mean == 250.0
    # 1M input at 0.15 + 1M output at 0.60, off-peak.
    assert record.cost is not None and record.cost.available
    assert record.cost.cost_usd == pytest.approx(0.75)
    assert registry.counter_value(METRIC_COST, model="deepseek-flash", rate="off-peak") == pytest.approx(0.75)


def test_cached_tokens_are_billed_at_the_cache_hit_rate(registry, monkeypatch):
    monkeypatch.setattr("app.observability.tokens.metrics", registry)

    record = record_model_call(
        "deepseek-flash",
        latency_ms=1.0,
        usage=Usage(input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000),
    )

    # Every input token was a cache hit: 0.003 per 1M, not 0.15.
    assert record.cost is not None
    assert record.cost.cost_usd == pytest.approx(0.003)
    assert registry.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="cached") == 1_000_000


def test_cost_is_unavailable_for_an_unlisted_model(registry, monkeypatch):
    """`deepseek-chat` has no verified price, so no number may be invented."""
    monkeypatch.setattr("app.observability.tokens.metrics", registry)

    record = record_provider_usage(
        "deepseek-chat",
        {"prompt_tokens": 10, "completion_tokens": 10},
        latency_ms=5.0,
    )

    assert record.cost is not None
    assert record.cost.available is False
    assert record.cost.cost_usd is None
    assert "no verified price" in record.cost.unavailable_reason
    assert registry.counter_value(METRIC_COST, model="deepseek-chat") == 0.0
    assert (
        registry.counter_value(
            METRIC_COST_UNAVAILABLE,
            model="deepseek-chat",
            reason=record.cost.unavailable_reason,
        )
        == 1.0
    )


def test_a_call_without_usage_is_reported_as_unreported_not_as_zero(registry, monkeypatch):
    monkeypatch.setattr("app.observability.tokens.metrics", registry)

    record = record_model_call("deepseek-flash", latency_ms=9.0, usage=None)

    assert record.usage is None
    assert record.cost is None
    assert (
        registry.counter_value(METRIC_USAGE_REPORTED, model="deepseek-flash", reported="false")
        == 1.0
    )
    assert registry.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="input") == 0.0


def test_price_table_lookup_tolerates_release_suffixes():
    table = PriceTable()
    assert table.price_for("deepseek-flash") is not None
    assert table.price_for("deepseek-flash-0731") is table.price_for("deepseek-flash")
    assert table.price_for("no-such-model") is None
    assert table.price_for("") is None


def test_price_table_leaves_an_unknown_model_unpriced_entirely():
    table = PriceTable({"known": ModelPrice(model="known", input_per_mtok=1.0, output_per_mtok=2.0)})
    estimate = table.estimate("other", input_tokens=10, output_tokens=10)
    assert estimate.cost_usd is None
    assert estimate.available is False


def test_cached_tokens_without_a_cached_price_are_unavailable():
    """Guessing the cache-hit rate would under-report spend; say so instead."""
    table = PriceTable(
        {"m": ModelPrice(model="m", input_per_mtok=1.0, output_per_mtok=1.0, cached_input_per_mtok=None)}
    )
    estimate = table.estimate("m", input_tokens=100, output_tokens=0, cached_tokens=50)
    assert estimate.cost_usd is None
    assert "cache-hit" in estimate.unavailable_reason


def test_cost_override_wins_over_the_table(registry, monkeypatch):
    monkeypatch.setattr("app.observability.tokens.metrics", registry)
    table = PriceTable({"m": ModelPrice(model="m", input_per_mtok=1.0, output_per_mtok=1.0)})
    estimate = table.estimate("m", input_tokens=1, output_tokens=1)
    record = record_model_call(
        "m", latency_ms=1.0, usage=Usage(input_tokens=1, output_tokens=1), cost=estimate
    )
    assert record.cost is not None
    assert record.cost.cost_usd == pytest.approx(2e-06)


def test_price_table_environment_override(monkeypatch):
    payload = json.dumps(
        {
            "my-model": {
                "input_per_mtok": 1.5,
                "output_per_mtok": 3.0,
                "cached_input_per_mtok": 0.1,
                "source": "unit-test-rate-card",
            }
        }
    )
    table = load_price_table({"OBSERVABILITY_PRICE_TABLE_JSON": payload})
    assert table.models == ["my-model"]
    assert table.price_for("my-model").input_per_mtok == 1.5
    assert table.describe("my-model")["source"] == "unit-test-rate-card"


def test_price_table_falls_back_on_a_broken_override():
    table = load_price_table({"OBSERVABILITY_PRICE_TABLE_JSON": "{not json"})
    assert table.models == sorted(DEFAULT_PRICES)


def test_price_table_records_the_rate_basis_and_peak_choice():
    assert PriceTable().rate == OFF_PEAK
    assert PriceTable(rate=PEAK).rate == PEAK
    # An unknown rate value degrades to off-peak rather than failing to start.
    assert PriceTable(rate="nonsense").rate == OFF_PEAK


def test_default_prices_never_target_an_unverified_model():
    """`deepseek-chat` is a retired name with no published price."""
    assert "deepseek-chat" not in DEFAULT_PRICES
    for price in DEFAULT_PRICES.values():
        assert price.source.startswith("https://api-docs.deepseek.com")


# ------------------------------------------------------------- trace functions


def test_trace_event_records_into_the_registry(monkeypatch):
    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tracing.metrics", local)

    trace_event("retrieval.done", {"chunks": 3})
    trace_event("retrieval.done", {"chunks": 1})

    assert local.counter_value("trace.events") == 2.0


def test_trace_event_never_raises_on_a_bad_attribute(monkeypatch):
    class Exploding(dict):
        def items(self):  # pragma: no cover - defined to blow up
            raise RuntimeError("boom")

    monkeypatch.setattr("app.observability.tracing.otlp.otlp_endpoint", lambda: "")
    trace_event("bad", Exploding())  # must not raise


def test_trace_model_call_records_tokens_and_cost(monkeypatch):
    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr("app.observability.tracing.metrics", local)
    monkeypatch.setattr("app.observability.tracing.otlp.otlp_endpoint", lambda: "")

    record = trace_model_call("deepseek-flash", 1000, 100, 42, cached_tokens=500)

    assert record is not None
    assert record.usage is not None and record.usage.cached_tokens == 500
    assert local.counter_value(METRIC_CALLS, model="deepseek-flash", outcome="ok") == 1.0


def test_trace_token_usage_handles_a_streaming_usage_frame(monkeypatch):
    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr("app.observability.tracing.metrics", local)

    record = trace_token_usage(
        "deepseek-flash",
        {"prompt_tokens": 20, "completion_tokens": 10},
        latency_ms=12.5,
    )
    assert record is not None and record.usage is not None
    assert local.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="output") == 10.0


def test_observability_status_is_honest_about_what_is_not_active(monkeypatch):
    monkeypatch.setattr(
        "app.observability.tracing.settings.langfuse_public_key", "pk", raising=False
    )
    monkeypatch.setattr(
        "app.observability.tracing.settings.langfuse_secret_key", "sk", raising=False
    )
    monkeypatch.setattr(
        "app.observability.tracing.settings.otel_exporter_otlp_endpoint", "", raising=False
    )

    status = observability_status()
    assert status["registry"]["active"] is True
    assert status["otlp"]["endpoint_configured"] is False
    assert status["otlp"]["auto_instrumentation"] is False
    assert status["langfuse"]["configured"] is True
    assert status["langfuse"]["active"] is False
    assert status["langfuse"]["state"] == "configured-but-not-exporting"


# --------------------------------------------- usage capture in the LLM client


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def aread(self) -> bytes:
        return b"upstream exploded"

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    def stream(self, *_args, **_kwargs) -> _FakeStreamResponse:
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def post(self, *_args, **_kwargs):
        raise AssertionError("generate_title is not exercised by this fake")


def _patch_stream(monkeypatch, response: _FakeStreamResponse):
    monkeypatch.setattr(
        "app.gateway.litellm.httpx.AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(response),
    )


def _usage_frame() -> str:
    return "data: " + json.dumps(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 800,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 768},
            },
        }
    )


def test_stream_completion_records_provider_usage(monkeypatch):
    from app.gateway import litellm

    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr(litellm.settings, "deepseek_api_key", "test-key", raising=False)

    response = _FakeStreamResponse(
        [
            'data: {"choices": [{"delta": {"content": "你好"}}]}',
            _usage_frame(),
            "data: [DONE]",
        ]
    )
    _patch_stream(monkeypatch, response)

    async def scenario():
        chunks = [
            chunk
            async for chunk in litellm.stream_completion(
                messages=[{"role": "user", "content": "hi"}],
                system="sys",
                model="deepseek-flash",
            )
        ]
        return chunks

    import asyncio

    chunks = asyncio.run(scenario())

    assert chunks == [("answer", "你好")]
    assert (
        local.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="input") == 800.0
    )
    assert (
        local.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="cached") == 768.0
    )
    assert (
        local.counter_value(METRIC_TOKENS, model="deepseek-flash", kind="output") == 40.0
    )
    assert (
        local.counter_value(
            METRIC_USAGE_REPORTED, model="deepseek-flash", reported="true"
        )
        == 1.0
    )


def test_stream_completion_requests_usage_and_marks_a_error_outcome(monkeypatch):
    from app.gateway import litellm

    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr(litellm.settings, "deepseek_api_key", "test-key", raising=False)

    seen: dict[str, object] = {}

    class RecordingResponse(_FakeStreamResponse):
        pass

    response = RecordingResponse([], status_code=500)

    def factory(**kwargs):
        client = _FakeAsyncClient(response)
        original_stream = client.stream

        def stream(method, url, **stream_kwargs):
            seen["json"] = stream_kwargs.get("json")
            return original_stream(method, url, **stream_kwargs)

        client.stream = stream  # type: ignore[method-assign]
        return client

    monkeypatch.setattr("app.gateway.litellm.httpx.AsyncClient", factory)

    async def scenario():
        with pytest.raises(RuntimeError):
            async for _ in litellm.stream_completion(
                messages=[{"role": "user", "content": "hi"}],
                system="sys",
                model="deepseek-flash",
            ):
                pass

    import asyncio

    asyncio.run(scenario())

    assert seen["json"]["stream_options"] == {"include_usage": True}
    assert (
        local.counter_value(METRIC_CALLS, model="deepseek-flash", outcome="error") == 1.0
    )


def test_stream_completion_without_a_key_is_not_counted_as_a_model_call(monkeypatch):
    from app.gateway import litellm

    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.tokens.metrics", local)
    monkeypatch.setattr(litellm.settings, "deepseek_api_key", "", raising=False)

    async def scenario():
        return [
            chunk
            async for chunk in litellm.stream_completion(
                messages=[{"role": "user", "content": "hi"}], system="sys"
            )
        ]

    import asyncio

    chunks = asyncio.run(scenario())

    assert chunks and chunks[0][0] == "answer"
    assert local.counter_value(METRIC_CALLS) == 0.0


# ------------------------------------------------- best-effort OTLP exporting


def test_otlp_export_is_skipped_without_an_endpoint(monkeypatch):
    from app.observability import otlp

    otlp.reset_exporter_state()
    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.otlp.metrics", local)

    def explode(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("no HTTP call may happen without an endpoint")

    monkeypatch.setattr("app.observability.otlp.httpx.post", explode)

    assert otlp.export_span("x", {"a": 1}) is False
    assert local.counter_value("otlp.spans.skipped", reason="no-endpoint") == 1.0
    otlp.reset_exporter_state()


def test_otlp_export_posts_a_valid_payload_and_drops_secrets(monkeypatch):
    from app.observability import otlp

    otlp.reset_exporter_state()
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs["timeout"]
        return _Response()

    monkeypatch.setattr("app.observability.otlp.httpx.post", fake_post)

    ok = otlp.export_span(
        "llm.completion",
        {
            "gen_ai.request.model": "deepseek-flash",
            "gen_ai.usage.input_tokens": 10,
            "prompt": "SECRET PROMPT",
            "user_id": "u1",
        },
        duration_ms=10.0,
        endpoint="http://collector:4318",
    )

    assert ok is True
    assert captured["url"] == "http://collector:4318/v1/traces"
    assert captured["timeout"] == otlp.EXPORT_TIMEOUT_SECONDS
    encoded = json.dumps(captured["json"])
    assert "SECRET PROMPT" not in encoded
    assert "u1" not in encoded
    span = captured["json"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["name"] == "llm.completion"
    assert span["traceId"] and span["spanId"]
    keys = {attribute["key"] for attribute in span["attributes"]}
    assert "gen_ai.request.model" in keys
    assert "prompt" not in keys and "user_id" not in keys


def test_otlp_traces_url_accepts_a_fully_qualified_endpoint():
    from app.observability import otlp

    assert otlp.otlp_traces_url("http://c:4318") == "http://c:4318/v1/traces"
    assert otlp.otlp_traces_url("http://c:4318/") == "http://c:4318/v1/traces"
    assert (
        otlp.otlp_traces_url("http://c:4318/v1/traces") == "http://c:4318/v1/traces"
    )
    assert otlp.otlp_traces_url("") == ""


def test_otlp_failure_never_raises_into_the_caller(monkeypatch):
    from app.observability import otlp

    otlp.reset_exporter_state()
    local = MetricsRegistry()
    monkeypatch.setattr("app.observability.otlp.metrics", local)

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("collector down")

    monkeypatch.setattr("app.observability.otlp.httpx.post", boom)

    assert otlp.export_span("x", {}, endpoint="http://collector:4318") is False
    assert local.counter_value("otlp.spans.failed") == 1.0
    assert otlp.exporter_state()["last_error"].startswith("ConnectError")
    otlp.reset_exporter_state()


def test_trace_event_exports_a_span_when_an_endpoint_is_set(monkeypatch):
    from app.observability import otlp, tracing

    otlp.reset_exporter_state()
    monkeypatch.setattr(tracing, "otlp", otlp)
    monkeypatch.setattr(
        tracing.settings, "otel_exporter_otlp_endpoint", "http://collector:4318", raising=False
    )
    captured: dict[str, object] = {}

    class _Response:
        status_code = 202

    monkeypatch.setattr(
        otlp.httpx,
        "post",
        lambda url, **kwargs: (captured.update(url=url, json=kwargs["json"]), _Response())[1],
    )

    trace_event("answer.generated", {"model": "deepseek-flash", "content": "SECRET"})

    assert captured["url"] == "http://collector:4318/v1/traces"
    assert "SECRET" not in json.dumps(captured["json"])
    otlp.reset_exporter_state()
    monkeypatch.setattr(
        tracing.settings, "otel_exporter_otlp_endpoint", "", raising=False
    )
