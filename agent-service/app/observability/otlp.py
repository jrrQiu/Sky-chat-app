"""Best-effort OTLP/HTTP span export, without the OpenTelemetry SDK.

The honest position this module takes: this repository does **not** run
SDK-grade OpenTelemetry instrumentation. There is no auto-instrumentation of
FastAPI or of the HTTP client, no context propagation into background tasks and
no batch span processor. What is implemented here is the smallest thing that is
still real and still useful when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set:

* a span is built in the OTLP/JSON encoding,
* it is POSTed with the ``httpx`` client that is already a dependency,
* with a short timeout, on a best-effort basis.

Two hard rules, because this runs inside the request path:

1. **Never raise.** Any transport, encoding or configuration problem increments
   a counter and is logged once; the caller is unaffected.
2. **Never send content.** Only the span name, the model name, token counts,
   durations and the configured service name leave the process. No prompt text,
   no message content, no user id. The attribute builder drops anything that is
   not a scalar or a short scalar list and refuses reserved keys.

Because it is not the OpenTelemetry SDK, the limitation is stated in the code
and in ``observability_status()`` (surfaced by ``/metrics`` and
``/v1/observability/status``) rather than hidden behind a stub comment.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from app.config import settings
from app.observability.registry import metrics

logger = logging.getLogger(__name__)

#: Export timeout. Deliberately far below the LLM timeout: a telemetry export
#: must never be a meaningful part of a request.
EXPORT_TIMEOUT_SECONDS = 2.0

#: Attribute keys that must never be exported even if a caller passes them.
_FORBIDDEN_ATTRIBUTE_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "message",
        "content",
        "answer",
        "question",
        "user_id",
        "userid",
        "user",
        "authorization",
        "api_key",
        "token",
        "secret",
        "password",
    }
)

MAX_ATTRIBUTE_LENGTH = 256
MAX_ATTRIBUTES = 32

METRIC_EXPORTED = "otlp.spans.exported"
METRIC_EXPORT_FAILED = "otlp.spans.failed"
METRIC_EXPORT_SKIPPED = "otlp.spans.skipped"

HELP_EXPORTED = "OTLP spans accepted by the configured collector."
HELP_EXPORT_FAILED = "OTLP span exports that failed; telemetry only, never the request."
HELP_SKIPPED = "OTLP spans skipped because no endpoint is configured."

_status_lock = threading.Lock()
_exported = 0
_failed = 0
_skipped = 0
_last_error = ""
_warned_about_failure = False


def otlp_endpoint() -> str:
    """The configured OTLP/HTTP endpoint, or an empty string."""
    return (settings.otel_exporter_otlp_endpoint or "").strip()


def otlp_traces_url(endpoint: str | None = None) -> str:
    """Resolve the traces path of an OTLP base URL.

    Mirrors how the LLM client tolerates a base URL that already ends in its own
    path, so both ``http://collector:4318`` and a fully qualified
    ``.../v1/traces`` work.
    """
    base = (endpoint if endpoint is not None else otlp_endpoint()).rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def _hex_id(byte_count: int) -> str:
    return uuid.uuid4().hex[: byte_count * 2]


def _now_unix_nano() -> int:
    return time.time_ns()


def _otlp_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value[:MAX_ATTRIBUTE_LENGTH]}
    return None


def _attributes(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Build OTLP attributes, dropping anything unsafe or non-scalar."""
    if not payload:
        return []
    attributes: list[dict[str, Any]] = []
    for raw_key, value in payload.items():
        key = str(raw_key)
        if key.strip().lower() in _FORBIDDEN_ATTRIBUTE_KEYS:
            continue
        encoded = _otlp_value(value)
        if encoded is None:
            continue
        attributes.append({"key": key, "value": encoded})
        if len(attributes) >= MAX_ATTRIBUTES:
            break
    return attributes


def _resource() -> dict[str, Any]:
    return {
        "attributes": [
            {
                "key": "service.name",
                "value": {"stringValue": "sky-chat-agent-service"},
            },
            {
                "key": "deployment.environment",
                "value": {"stringValue": settings.environment},
            },
        ]
    }


@dataclass
class SpanPayload:
    """One span, ready for OTLP/JSON encoding."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""
    start_time_unix_nano: int = 0
    end_time_unix_nano: int = 0

    def to_otlp_span(self) -> dict[str, Any]:
        ended = self.end_time_unix_nano or _now_unix_nano()
        started = self.start_time_unix_nano or ended
        return {
            "traceId": self.trace_id or _hex_id(16),
            "spanId": self.span_id or _hex_id(8),
            "name": self.name[:128],
            "kind": 1,  # SPAN_KIND_INTERNAL
            "startTimeUnixNano": str(started),
            "endTimeUnixNano": str(max(started, ended)),
            "attributes": _attributes(self.attributes),
            "status": {"code": 1},  # STATUS_CODE_OK
        }


def build_otlp_payload(spans: list[SpanPayload]) -> dict[str, Any]:
    """Wrap spans in the OTLP/JSON ``ExportTraceServiceRequest`` shape."""
    return {
        "resourceSpans": [
            {
                "resource": _resource(),
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "app.observability.tracing",
                            "version": "1.0.0",
                        },
                        "spans": [span.to_otlp_span() for span in spans],
                    }
                ],
            }
        ]
    }


def _note_exported(count: int) -> None:
    global _exported
    with _status_lock:
        _exported += count
    metrics.counter(METRIC_EXPORTED, HELP_EXPORTED).inc(count)


def _note_skipped(count: int, reason: str) -> None:
    global _skipped
    with _status_lock:
        _skipped += count
    metrics.counter(METRIC_EXPORT_SKIPPED, HELP_SKIPPED, reason=reason).inc(count)


def _note_failure(error: str) -> None:
    global _failed, _last_error, _warned_about_failure
    with _status_lock:
        _failed += 1
        _last_error = error[:200]
        should_warn = not _warned_about_failure
        _warned_about_failure = True
    metrics.counter(METRIC_EXPORT_FAILED, HELP_EXPORT_FAILED).inc()
    if should_warn:  # log the first failure only; the counter carries the rest
        logger.warning("OTLP span export failed: %s", error)


def export_spans(spans: list[SpanPayload], *, endpoint: str | None = None) -> bool:
    """POST spans to the collector. Returns success. Never raises."""
    if not spans:
        return True
    url = otlp_traces_url(endpoint)
    if not url:
        _note_skipped(len(spans), "no-endpoint")
        return False

    try:
        payload = build_otlp_payload(spans)
        response = httpx.post(
            url,
            json=payload,
            timeout=EXPORT_TIMEOUT_SECONDS,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            _note_failure(f"HTTP {response.status_code}")
            return False
    except Exception as exc:  # noqa: BLE001 - telemetry must never propagate
        _note_failure(f"{type(exc).__name__}: {exc}")
        return False

    _note_exported(len(spans))
    return True


def export_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    duration_ms: float | None = None,
    endpoint: str | None = None,
) -> bool:
    """Convenience wrapper for a single finished span."""
    ended = _now_unix_nano()
    started = ended - int((duration_ms or 0.0) * 1_000_000)
    return export_spans(
        [
            SpanPayload(
                name=name,
                attributes=dict(attributes or {}),
                start_time_unix_nano=started,
                end_time_unix_nano=ended,
            )
        ],
        endpoint=endpoint,
    )


def exporter_state() -> dict[str, Any]:
    """Snapshot for the status endpoints and for tests."""
    with _status_lock:
        exported, failed, skipped, last_error = (
            _exported,
            _failed,
            _skipped,
            _last_error,
        )
    endpoint = otlp_endpoint()
    return {
        "endpoint_configured": bool(endpoint),
        "endpoint": endpoint,
        "traces_url": otlp_traces_url(endpoint),
        "exported_spans": exported,
        "failed_exports": failed,
        "skipped_spans": skipped,
        "last_error": last_error,
        "timeout_seconds": EXPORT_TIMEOUT_SECONDS,
        # Stated plainly so a reader of /metrics does not assume more than this.
        "instrumentation": "manual-best-effort",
        "auto_instrumentation": False,
    }


def reset_exporter_state() -> None:
    """Reset counters. For tests only."""
    global _exported, _failed, _skipped, _last_error, _warned_about_failure
    with _status_lock:
        _exported = 0
        _failed = 0
        _skipped = 0
        _last_error = ""
        _warned_about_failure = False


def _json_dumps(payload: Mapping[str, Any]) -> str:
    """Stable JSON encoding used by tests to assert the export body."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "EXPORT_TIMEOUT_SECONDS",
    "SpanPayload",
    "build_otlp_payload",
    "export_span",
    "export_spans",
    "exporter_state",
    "otlp_endpoint",
    "otlp_traces_url",
    "reset_exporter_state",
    "_json_dumps",
]
