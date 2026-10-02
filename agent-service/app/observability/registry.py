"""In-process metrics registry: the single source of truth for what happened.

An evaluation or a cost report is only meaningful if it is grounded in counters
the process actually incremented, so this module is deliberately the *only*
place where observations are stored. Everything else — the Prometheus
endpoint, the cost report, the evaluation harness — reads from here.

Design decisions worth stating:

* **No new dependency.** `prometheus_client` is not in `requirements.txt`, so
  the registry keeps its own counters and renders the Prometheus text format
  itself. That keeps the dependency surface unchanged while still producing an
  exposition format a scraper can parse.
* **Thread-safe, not process-safe.** FastAPI runs sync endpoints and graph
  callbacks on an anyio worker thread pool, so a single lock-guarded dict is
  required for correctness. Behind multiple replicas the numbers are
  per-replica, exactly like the rate limiter's in-process fallback; that is
  stated on the endpoint instead of pretending a global aggregate exists.
* **Cardinality is bounded by construction.** Call sites pass a fixed label set
  (`model`, `kind`, `outcome`). There is no API for free-form labels such as a
  user id or a prompt, which is what keeps the endpoint safe to expose.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

# Fixed buckets in milliseconds. They straddle a fast local model call, a
# retrieval round trip, and the configured LLM timeout, so a histogram is
# actually informative rather than everything landing in one bucket.
DEFAULT_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
    30000.0,
    60000.0,
)

COUNTER = "counter"
HISTOGRAM = "histogram"

_LABEL_SEPARATOR = "\x1f"


def _sanitize_metric_name(name: str) -> str:
    """Map a dotted call-site name onto a valid Prometheus metric name."""
    cleaned = []
    for index, char in enumerate(name):
        if char.isalnum() or char == "_":
            cleaned.append(char)
        elif char in {".", "-", "/", " "}:
            cleaned.append("_")
        elif char == ":":
            cleaned.append(":")
        else:
            cleaned.append("_")
    rendered = "".join(cleaned).strip("_")
    if not rendered:
        rendered = "unnamed_metric"
    if rendered[0].isdigit():
        rendered = f"m_{rendered}"
    return rendered


def _sanitize_label_name(name: str) -> str:
    rendered = "".join(char if (char.isalnum() or char == "_") else "_" for char in name)
    if not rendered:
        rendered = "label"
    if rendered[0].isdigit():
        rendered = f"l_{rendered}"
    return rendered


def _sanitize_label_value(value: object) -> str:
    """Never let a label value escape the exposition format.

    Truncation plus escaping means a caller cannot forge a metric line by
    getting a newline or a quote into a label value.
    """
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return text[:200]


def _format_number(value: float) -> str:
    if isinstance(value, bool):  # bool is an int subclass; keep it explicit
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "0"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _label_key(labels: Mapping[str, object]) -> tuple[str, ...]:
    """Build the series key. Label names are sanitized, then sorted."""
    return tuple(
        f"{_sanitize_label_name(name)}={_sanitize_label_value(labels[name])}"
        for name in sorted(labels, key=_sanitize_label_name)
    )


@dataclass
class MetricFamily:
    """One metric name with all of its label sets."""

    name: str
    kind: str
    help_text: str
    label_names: tuple[str, ...]
    # label values (sorted to match `label_names`) -> value or histogram state
    series: dict[tuple[str, ...], float] = field(default_factory=dict)
    counts: dict[tuple[str, ...], int] = field(default_factory=dict)
    sums: dict[tuple[str, ...], float] = field(default_factory=dict)
    buckets: dict[tuple[str, ...], list[int]] = field(default_factory=dict)
    bucket_bounds: tuple[float, ...] = ()

    def render(self) -> list[str]:
        prometheus_name = _sanitize_metric_name(self.name)
        lines = [
            f"# HELP {prometheus_name} {self.help_text}",
            f"# TYPE {prometheus_name} {self.kind}",
        ]
        for key in sorted(self.series):
            label_text = ",".join(key)
            if self.kind == COUNTER:
                lines.append(
                    f"{prometheus_name}{{{label_text}}} "
                    f"{_format_number(self.series[key])}"
                    if label_text
                    else f"{prometheus_name} {_format_number(self.series[key])}"
                )
                continue

            count = self.counts.get(key, 0)
            total_sum = self.sums.get(key, 0.0)
            cumulative = 0
            observed = self.buckets.get(key, [])
            for index, bound in enumerate(self.bucket_bounds):
                observed_in_bucket = observed[index] if index < len(observed) else 0
                cumulative += observed_in_bucket
                bucket_labels = f'{label_text},le="{_format_number(bound)}"'
                lines.append(
                    f"{prometheus_name}_bucket{{{bucket_labels}}} {cumulative}"
                )
            infinite_labels = f'{label_text},le="+Inf"' if label_text else 'le="+Inf"'
            lines.append(f"{prometheus_name}_bucket{{{infinite_labels}}} {count}")
            if label_text:
                lines.append(f"{prometheus_name}_sum{{{label_text}}} {_format_number(total_sum)}")
                lines.append(f"{prometheus_name}_count{{{label_text}}} {count}")
            else:
                lines.append(f"{prometheus_name}_sum {_format_number(total_sum)}")
                lines.append(f"{prometheus_name}_count {count}")
        return lines


class Counter:
    """Handle for one label set of a counter metric."""

    __slots__ = ("_registry", "_name", "_labels")

    def __init__(
        self,
        registry: MetricsRegistry,
        name: str,
        labels: Mapping[str, object],
    ) -> None:
        self._registry = registry
        self._name = name
        self._labels = dict(labels)

    def inc(self, amount: float = 1.0) -> None:
        self._registry._increment(self._name, self._labels, amount)

    @property
    def value(self) -> float:
        return self._registry.counter_value(self._name, **self._labels)


class Timer:
    """Handle for one label set of a histogram/timer metric."""

    __slots__ = ("_registry", "_name", "_labels")

    def __init__(
        self,
        registry: MetricsRegistry,
        name: str,
        labels: Mapping[str, object],
    ) -> None:
        self._registry = registry
        self._name = name
        self._labels = dict(labels)

    def observe(self, milliseconds: float) -> None:
        self._registry._observe(self._name, self._labels, milliseconds)

    @contextmanager
    def time(self) -> Iterator[None]:
        """Record the wall-clock duration of the wrapped block, in ms."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe((time.perf_counter() - started) * 1000.0)


class MetricsRegistry:
    """Thread-safe in-process counter and histogram registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._families: dict[str, MetricFamily] = {}

    # --- writing ---------------------------------------------------------
    def counter(
        self,
        name: str,
        help_text: str = "",
        **labels: object,
    ) -> Counter:
        """Return a counter handle. Reuse the same label names for one metric."""
        declared = self._declare(name, COUNTER, help_text, labels)
        return Counter(self, name, declared)

    def timer(
        self,
        name: str,
        help_text: str = "",
        *,
        buckets_ms: tuple[float, ...] | None = None,
        **labels: object,
    ) -> Timer:
        """Return a histogram/timer handle measured in milliseconds."""
        declared = self._declare(
            name,
            HISTOGRAM,
            help_text,
            labels,
            bucket_bounds=buckets_ms or DEFAULT_LATENCY_BUCKETS_MS,
        )
        return Timer(self, name, declared)

    def _declare(
        self,
        name: str,
        kind: str,
        help_text: str,
        labels: Mapping[str, object],
        bucket_bounds: tuple[float, ...] | None = None,
    ) -> dict[str, object]:
        # Sanitize once, here, so every later `_label_key` call is consistent
        # between the handle and the family it writes into.
        sanitized = {
            _sanitize_label_name(str(key)): value for key, value in labels.items()
        }
        label_names = tuple(sorted(sanitized))
        with self._lock:
            family = self._families.get(name)
            if family is None:
                family = MetricFamily(
                    name=name,
                    kind=kind,
                    help_text=help_text or name,
                    label_names=label_names,
                    bucket_bounds=tuple(bucket_bounds or ()),
                )
                self._families[name] = family
            key = _label_key(sanitized)
            if kind == COUNTER:
                family.series.setdefault(key, 0.0)
            else:
                family.series.setdefault(key, 0.0)
                family.counts.setdefault(key, 0)
                family.sums.setdefault(key, 0.0)
                family.buckets.setdefault(key, [0] * len(family.bucket_bounds))
        return sanitized

    def _increment(
        self, name: str, labels: Mapping[str, object], amount: float
    ) -> None:
        key = _label_key(labels)
        with self._lock:
            family = self._families[name]
            family.series[key] = family.series.get(key, 0.0) + amount

    def _observe(
        self, name: str, labels: Mapping[str, object], milliseconds: float
    ) -> None:
        key = _label_key(labels)
        with self._lock:
            family = self._families[name]
            family.series[key] = family.series.get(key, 0.0) + milliseconds
            family.counts[key] = family.counts.get(key, 0) + 1
            family.sums[key] = family.sums.get(key, 0.0) + milliseconds
            observed = family.buckets.setdefault(
                key, [0] * len(family.bucket_bounds)
            )
            for index, bound in enumerate(family.bucket_bounds):
                if milliseconds <= bound:
                    observed[index] += 1
                    break

    # --- reading ---------------------------------------------------------
    def counter_value(self, name: str, **labels: object) -> float:
        with self._lock:
            family = self._families.get(name)
            if family is None:
                return 0.0
            return family.series.get(_label_key(labels), 0.0)

    def histogram_stats(self, name: str, **labels: object) -> tuple[int, float]:
        """Return ``(count, mean)`` for one histogram label set."""
        with self._lock:
            family = self._families.get(name)
            if family is None:
                return (0, 0.0)
            key = _label_key(labels)
            count = family.counts.get(key, 0)
            total = family.sums.get(key, 0.0)
        return (count, total / count if count else 0.0)

    def snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        """Immutable view for tests and for the JSON status endpoint."""
        with self._lock:
            result: dict[str, Mapping[str, Any]] = {}
            for name, family in self._families.items():
                series: dict[str, Any] = {}
                for key in sorted(family.series):
                    label_text = ",".join(key)
                    if family.kind == COUNTER:
                        series[label_text] = family.series[key]
                    else:
                        series[label_text] = {
                            "count": family.counts.get(key, 0),
                            "sum": family.sums.get(key, 0.0),
                        }
                result[name] = MappingProxyType(
                    {
                        "kind": family.kind,
                        "help": family.help_text,
                        "series": MappingProxyType(series),
                    }
                )
            return MappingProxyType(result)

    def reset(self) -> None:
        """Drop everything. Only for tests and long-lived-process recycling."""
        with self._lock:
            self._families.clear()

    def render_prometheus(self) -> str:
        """Render the Prometheus text exposition format (version 0.0.4)."""
        with self._lock:
            families = sorted(self._families.values(), key=lambda item: item.name)
            lines: list[str] = []
            for family in families:
                lines.extend(family.render())
        return "\n".join(lines) + "\n"


# The process-wide registry. One instance per process, imported everywhere.
metrics = MetricsRegistry()
