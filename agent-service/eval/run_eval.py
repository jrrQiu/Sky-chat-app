"""Evaluation gate: a per-metric pass/fail table and an exit code.

The harness used to print JSON and always exit 0, which made it impossible to
put in a pipeline: a retrieval regression would be reported and then ignored.
The exit code is now the contract:

===== =========================================================
code  meaning
===== =========================================================
0     every gated metric passed, and every required metric was available
1     at least one threshold was breached, or a required metric was unavailable
2     the invocation itself was wrong (unknown metric, unreadable thresholds file)
===== =========================================================

Usage::

    python -m eval.run_eval                       # default thresholds
    python -m eval.run_eval --thresholds-file eval/thresholds.json
    python -m eval.run_eval --set recall_at_k=0.9 --set mrr=0.8
    python -m eval.run_eval --require citation_accuracy
    python -m eval.run_eval --judge --require groundedness
    python -m eval.run_eval --json eval-report.json

`groundedness` genuinely needs an LLM judge. It is therefore **not** required by
default, because failing every CI run for a metric that was never measured is
just noise. Making it required is an explicit choice (``--require groundedness``
or ``unavailable: "fail"``), and when it is required and unavailable the run
fails — it never substitutes a placeholder.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from eval.metrics import EvaluationReport, evaluate_retrieval

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_USAGE = 2

#: Every metric the gate knows how to read off the report.
KNOWN_METRICS = (
    "recall_at_k",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "citation_accuracy",
    "groundedness",
    "p95_latency_ms",
    # Integrity of the assembled prompt's citation map. Measured only when the
    # harness is given an assembled context, so it defaults to not-required.
    "context_citation_accuracy",
)

MAX_MEASURED_METRICS = {
    "recall_at_k",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "citation_accuracy",
    "p95_latency_ms",
    "context_citation_accuracy",
}

#: Defaults for the metrics that are always measured. `p95_latency_ms` is left
#: ungated because a latency budget belongs to the deployment, not to this
#: harness; set it in a thresholds file when a CI machine has a known budget.
DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "recall_at_k": {"min": 0.9, "required": True},
    "mrr": {"min": 0.8, "required": True},
    "ndcg_at_k": {"min": 0.8, "required": True},
    "precision_at_k": {"min": 0.5, "required": True},
    "citation_accuracy": {"min": 1.0, "required": True},
    # Optional by default: measuring it needs a judge. `--require groundedness`
    # turns a missing judgement into a failure.
    "groundedness": {"min": 0.7, "required": False},
    # A dangling citation in the assembled prompt is a hard defect, so the bar is
    # 1.0 -- but it is only available on the context path, hence not required.
    "context_citation_accuracy": {"min": 1.0, "required": False},
}

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_NOT_GATED = "not-gated"


@dataclass
class MetricThreshold:
    """One metric's gate configuration."""

    name: str
    min_value: float | None = None
    max_value: float | None = None
    required: bool = False
    #: ``auto`` honours `required`; ``fail`` always fails an unavailable metric;
    #: ``skip`` never fails one.
    unavailable_action: str = "auto"

    @classmethod
    def from_config(cls, name: str, config: Mapping[str, Any] | None) -> MetricThreshold:
        config = dict(config or {})
        action = str(config.get("unavailable", "auto")).lower()
        if action not in {"auto", "fail", "skip"}:
            raise ValueError(
                f"{name}: unavailable must be one of auto/fail/skip, got {action!r}"
            )
        return cls(
            name=name,
            min_value=_optional_float(config.get("min")),
            max_value=_optional_float(config.get("max")),
            required=bool(config.get("required", False)),
            unavailable_action=action,
        )

    def required_for_gate(self, *, force_required: bool = False) -> bool:
        """Must an unavailable value fail the run?

        ``unavailable: "skip"`` wins over `required`, because an operator who
        says a metric may be skipped and separately marks it required is
        expressing "gate it when it is there". ``--require`` (force_required)
        overrides even that, because it is an explicit demand for this run.
        """
        if force_required:
            return True
        if self.unavailable_action == "skip":
            return False
        if self.unavailable_action == "fail":
            return True
        return self.required

    def to_dict(self) -> dict[str, Any]:
        return {
            "min": self.min_value,
            "max": self.max_value,
            "required": self.required,
            "unavailable": self.unavailable_action,
        }

    def evaluate(self, value: float | None) -> tuple[str, str]:
        """Return ``(status, detail)`` for one measured value."""
        if value is None:
            return STATUS_UNAVAILABLE, "metric was not measured"
        if not math.isfinite(value):
            return STATUS_UNAVAILABLE, "metric is not a finite number"
        if self.min_value is not None and value < self.min_value:
            return STATUS_FAIL, f"below min {self.min_value:g}"
        if self.max_value is not None and value > self.max_value:
            return STATUS_FAIL, f"above max {self.max_value:g}"
        return STATUS_PASS, ""


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


@dataclass
class GateRow:
    metric: str
    value: float | None
    status: str
    detail: str
    threshold: MetricThreshold | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "status": self.status,
            "detail": self.detail,
            "threshold": self.threshold.to_dict() if self.threshold else None,
        }


@dataclass
class GateResult:
    passed: bool
    exit_code: int
    rows: list[GateRow] = field(default_factory=list)

    @property
    def failures(self) -> list[GateRow]:
        return [row for row in self.rows if row.status == STATUS_FAIL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "failures": [row.metric for row in self.failures],
            "rows": [row.to_dict() for row in self.rows],
        }


def load_thresholds(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read a thresholds JSON file.

    Accepts either a flat ``{"recall_at_k": 0.9}`` mapping or the full
    ``{"recall_at_k": {"min": 0.9, "required": true}}`` form.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("thresholds file must be a JSON object")

    resolved: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if name not in KNOWN_METRICS:
            raise ValueError(f"unknown metric in thresholds file: {name!r}")
        if isinstance(value, Mapping):
            resolved[name] = dict(value)
        elif isinstance(value, (int, float)):
            resolved[name] = {"min": float(value)}
        else:
            raise ValueError(f"threshold for {name!r} must be a number or an object")
    return resolved


def parse_set_argument(argument: str) -> tuple[str, dict[str, Any]]:
    """Parse ``metric=min`` or ``metric.min=value`` from ``--set``."""
    if "=" not in argument:
        raise ValueError(f"--set expects metric=value, got {argument!r}")
    key, _, raw_value = argument.partition("=")
    key = key.strip()
    raw_value = raw_value.strip()
    if not key:
        raise ValueError(f"--set has no metric name: {argument!r}")

    if "." in key:
        metric, _, field_name = key.partition(".")
        metric = metric.strip()
        field_name = field_name.strip()
        if metric not in KNOWN_METRICS:
            raise ValueError(f"unknown metric: {metric!r}")
        if field_name not in {"min", "max", "required", "unavailable"}:
            raise ValueError(f"unknown threshold field: {field_name!r}")
        if field_name == "required":
            return metric, {"required": raw_value.lower() in {"1", "true", "yes"}}
        if field_name == "unavailable":
            return metric, {"unavailable": raw_value}
        return metric, {field_name: float(raw_value)}

    if key not in KNOWN_METRICS:
        raise ValueError(f"unknown metric: {key!r}")
    return key, {"min": float(raw_value)}


def build_gate(
    thresholds: Mapping[str, Mapping[str, Any]],
    *,
    require: Sequence[str] = (),
) -> dict[str, MetricThreshold]:
    """Merge defaults, file/CLI thresholds and forced requirements."""
    merged: dict[str, dict[str, Any]] = {
        name: dict(config) for name, config in DEFAULT_THRESHOLDS.items()
    }
    for name, config in thresholds.items():
        merged.setdefault(name, {}).update(config)

    forced = set(require)
    unknown = forced - set(KNOWN_METRICS)
    if unknown:
        raise ValueError(f"unknown metric in --require: {', '.join(sorted(unknown))}")

    gate: dict[str, MetricThreshold] = {}
    for name, config in merged.items():
        threshold = MetricThreshold.from_config(name, config)
        if name in forced:
            threshold.required = True
        gate[name] = threshold
    for name in sorted(forced - set(gate)):
        gate[name] = MetricThreshold(name=name, required=True)
    return gate


def evaluate_gate(
    report: EvaluationReport,
    gate: Mapping[str, MetricThreshold],
    *,
    require: Sequence[str] = (),
) -> GateResult:
    """Apply the gate to a report. Pure function, no printing, no exit."""
    forced = set(require)
    rows: list[GateRow] = []
    for name in KNOWN_METRICS:
        threshold = gate.get(name)
        value = getattr(report, name, None)
        value = None if value is None else float(value)
        if threshold is None:
            rows.append(
                GateRow(
                    metric=name,
                    value=value,
                    status=STATUS_NOT_GATED,
                    detail="no threshold configured",
                    threshold=None,
                )
            )
            continue

        status, detail = threshold.evaluate(value)
        if status == STATUS_UNAVAILABLE:
            if threshold.required_for_gate(force_required=name in forced):
                status = STATUS_FAIL
                detail = f"required but unavailable ({_report_reason(report, name)})"
        rows.append(
            GateRow(
                metric=name,
                value=value,
                status=status,
                detail=detail,
                threshold=threshold,
            )
        )

    passed = not any(row.status == STATUS_FAIL for row in rows)
    return GateResult(
        passed=passed,
        exit_code=EXIT_OK if passed else EXIT_GATE_FAILED,
        rows=rows,
    )


def _report_reason(report: EvaluationReport, name: str) -> str:
    if name == "groundedness":
        return report.groundedness_reason or "no reason recorded"
    if name == "context_citation_accuracy":
        return (
            "no assembled context was supplied to the harness "
            "(pass an answer_fn returning (answer, assembled_context) to measure it)"
        )
    return "no reason recorded"


def format_table(rows: Sequence[GateRow]) -> str:
    """Render the per-metric pass/fail table."""
    header = f"{'metric':<20} {'value':>12} {'min':>8} {'status':<12} detail"
    lines = [header, "-" * len(header)]
    for row in rows:
        if row.value is None:
            value_text = "n/a"
        elif row.metric == "p95_latency_ms":
            value_text = f"{row.value:.1f}ms"
        else:
            value_text = f"{row.value:.4f}"
        min_text = (
            f"{row.threshold.min_value:g}"
            if row.threshold and row.threshold.min_value is not None
            else "-"
        )
        lines.append(
            f"{row.metric:<20} {value_text:>12} {min_text:>8} "
            f"{row.status:<12} {row.detail}"
        )
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.run_eval",
        description="Run the retrieval evaluation and fail CI when a threshold is breached.",
    )
    parser.add_argument(
        "--thresholds-file",
        type=Path,
        default=None,
        help="JSON file of per-metric thresholds (min/max/required/unavailable).",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="METRIC=VALUE",
        help="Override one threshold, e.g. --set recall_at_k=0.95.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="METRIC",
        help="Force a missing metric to fail the run, e.g. --require groundedness.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Attempt the LLM groundedness judge (needs a configured API key).",
    )
    parser.add_argument("--k", type=int, default=5, help="Cut-off for precision/recall/nDCG.")
    parser.add_argument(
        "--latency-samples",
        type=int,
        default=30,
        help="Latency samples per query; 0 keeps the run fast.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        default=None,
        help="Also write the report and the gate table as JSON to this path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the table, not the full report JSON.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    evaluate: Callable[..., EvaluationReport] = evaluate_retrieval,
) -> int:
    """Run the harness. Returns the process exit code."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        thresholds: dict[str, dict[str, Any]] = {}
        if args.thresholds_file is not None:
            thresholds.update(load_thresholds(args.thresholds_file))
        for override in args.overrides:
            name, config = parse_set_argument(override)
            thresholds.setdefault(name, {}).update(config)
        gate = build_gate(thresholds, require=args.require)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    judge = None
    judge_note = "groundedness judge: not requested"
    if args.judge:
        try:
            from eval import judge as judge_module

            judge = judge_module.build_llm_judge()
        except Exception as exc:  # noqa: BLE001 - degrade to "unavailable"
            print(f"groundedness judge could not be built: {exc}", file=sys.stderr)
            judge = None
        judge_note = (
            f"groundedness judge: {judge.model} ({judge.prompt_version})"
            if judge is not None
            else "groundedness judge: requested but no model is callable; "
            "groundedness will be reported as unavailable"
        )

    try:
        report = evaluate(k=args.k, latency_samples=args.latency_samples, judge=judge)
    except Exception as exc:  # noqa: BLE001 - a broken run must not look green
        print(f"evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_GATE_FAILED
    result = evaluate_gate(report, gate, require=args.require)

    if args.json_path is not None:
        payload = {"report": report.to_dict(), "gate": result.to_dict()}
        args.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if not args.quiet:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(judge_note)
    if report.groundedness is None:
        print(f"groundedness unavailable: {report.groundedness_reason}")
    print()
    print(format_table(result.rows))
    print()
    if result.passed:
        print(f"gate: PASS (exit {result.exit_code})")
    else:
        failed = ", ".join(row.metric for row in result.failures)
        print(f"gate: FAIL - {failed} (exit {result.exit_code})")

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
