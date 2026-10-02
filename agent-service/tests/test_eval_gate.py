"""Tests for the evaluation gate and the two de-faked metrics.

Pins the contract the P1 work is judged on:

* `citation_accuracy` is 0 when a citation does not resolve to a retrieved id,
* `groundedness` is reported as unavailable rather than fabricated as 1.0,
* `run_eval` exits non-zero below a threshold and zero above it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pytest

from eval.metrics import (
    EvaluationReport,
    citation_accuracy_for_query,
    evaluate_retrieval,
    resolve_citation,
)
from eval.run_eval import (
    DEFAULT_THRESHOLDS,
    EXIT_GATE_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    build_gate,
    evaluate_gate,
    format_table,
    load_thresholds,
    main,
    parse_set_argument,
)


# ------------------------------------------------------------------- fixtures


def _evidence(source_id: str, citation: str, content: str = "正文内容") -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": source_id,
        "content": content,
        "source_type": "policy",
        "metadata": {"citation": citation},
    }


@dataclass(frozen=True)
class _Golden:
    query: str
    relevant: list[str]
    task_type: str


@dataclass
class _Result:
    evidence: list[dict[str, Any]]
    task_type: str = "policy_query"


class _StubJudge:
    """A judge with the same recorded metadata shape as the real one."""

    def __init__(self, score: float | None, prompt_version: str = "stub/v1") -> None:
        self.score = score
        self.prompt_version = prompt_version
        self.model = "deepseek-flash"
        self.calls: list[str] = []

    def __call__(self, query: str, evidence: Sequence[dict[str, Any]]) -> float | None:
        self.calls.append(query)
        return self.score


def _report(**overrides) -> EvaluationReport:
    values: dict[str, Any] = {
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 1.0,
        "precision_at_k": 1.0,
        "citation_accuracy": 1.0,
        "groundedness": None,
        "p95_latency_ms": 5.0,
        "query_details": [],
        "groundedness_reason": "groundedness requires an LLM judge",
    }
    values.update(overrides)
    return EvaluationReport(**values)


# ------------------------------------------------------ citation_accuracy


def test_a_citation_to_a_retrieved_id_resolves():
    check = resolve_citation("POL-VPN-001", "POL-VPN-001 v1.2", ["POL-VPN-001"])
    assert check.resolved is True


def test_a_citation_to_an_id_that_was_not_retrieved_fails():
    check = resolve_citation(
        "POL-HR-999", "POL-HR-999 v1", ["POL-VPN-001", "POL-VPN-SOP-001"]
    )
    assert check.resolved is False
    assert check.reason == "source id was not retrieved for this query"


def test_a_citation_whose_text_names_a_different_document_fails():
    """The rendered citation must name the item it is attached to."""
    check = resolve_citation("POL-VPN-001", "POL-HR-999 v1", ["POL-VPN-001"])
    assert check.resolved is False
    assert "does not name" in check.reason


def test_a_missing_citation_field_is_not_a_pass():
    check = resolve_citation("POL-VPN-001", "", ["POL-VPN-001"])
    assert check.resolved is False


def test_a_source_id_absent_from_evidence_is_not_a_pass():
    check = resolve_citation("", "POL-VPN-001 v1", ["POL-VPN-001"])
    assert check.resolved is False


def test_a_prefix_id_is_not_satisfied_by_a_longer_retrieved_id():
    check = resolve_citation("TC-1001", "工单 TC-1001-A", ["TC-1001-A"])
    assert check.resolved is False
    assert "not retrieved" in check.reason


def test_citation_accuracy_is_zero_when_a_citation_does_not_resolve():
    evidence = [_evidence("POL-VPN-001", "POL-HR-999 v1")]

    score, checks = citation_accuracy_for_query(evidence, ["POL-VPN-001"])

    assert score == 0.0
    assert len(checks) == 1 and checks[0].resolved is False


def test_citation_accuracy_averages_the_items():
    evidence = [
        _evidence("POL-VPN-001", "POL-VPN-001 v1"),
        _evidence("TC-1001", "工单 TC-9999"),
    ]

    score, checks = citation_accuracy_for_query(evidence, ["POL-VPN-001", "TC-1001"])

    assert score == 0.5
    assert [check.resolved for check in checks] == [True, False]


def test_citation_accuracy_is_one_when_every_citation_resolves():
    evidence = [
        _evidence("POL-VPN-001", "POL-VPN-001 v1"),
        _evidence("POL-VPN-SOP-001", "POL-VPN-SOP-001 v2"),
    ]
    score, _ = citation_accuracy_for_query(
        evidence, ["POL-VPN-001", "POL-VPN-SOP-001"]
    )
    assert score == 1.0


def test_an_answer_marker_past_the_evidence_fails():
    """`[7]` with two retrieved items is an unresolvable citation."""
    evidence = [_evidence("POL-VPN-001", "POL-VPN-001 v1")]

    score, checks = citation_accuracy_for_query(
        evidence, ["POL-VPN-001"], answer="根据制度 [1]，另有 [7] 条依据。"
    )

    assert score == 0.5
    assert any("not retrieved" in check.reason for check in checks)


def test_a_valid_answer_marker_keeps_the_score_at_one():
    evidence = [_evidence("POL-VPN-001", "POL-VPN-001 v1")]
    score, _ = citation_accuracy_for_query(
        evidence, ["POL-VPN-001"], answer="根据制度 [1] 规定。"
    )
    assert score == 1.0


def test_no_evidence_is_not_punished_by_the_citation_metric():
    score, _ = citation_accuracy_for_query([], [])
    assert score == 1.0


def test_real_corpus_citations_all_resolve_and_groundedness_is_unavailable():
    """The golden set is cited correctly, and nothing is fabricated."""
    report = evaluate_retrieval(latency_samples=0)

    assert report.citation_accuracy == 1.0
    assert report.citation_details == []
    assert report.groundedness is None
    assert report.groundedness_reason
    assert "judge" in report.groundedness_reason
    assert report.groundedness_judge == ""


def test_citation_failure_is_surfaced_in_the_report():
    result = _Result(evidence=[_evidence("POL-VPN-001", "POL-HR-999 v1")])

    report = evaluate_retrieval(
        run_fn=lambda _query: result,
        golden_queries=[_Golden("VPN 审批", ["POL-VPN-001"], "policy_query")],
        latency_samples=0,
    )

    assert report.citation_accuracy == 0.0
    assert report.citation_failures
    assert report.citation_failures[0]["source_id"] == "POL-VPN-001"


# --------------------------------------------------------- groundedness judge


def test_groundedness_is_unavailable_without_a_judge():
    report = evaluate_retrieval(latency_samples=0)
    assert report.groundedness is None
    assert "placeholder" in report.groundedness_reason


def test_groundedness_uses_the_judge_when_one_is_supplied():
    result = _Result(evidence=[_evidence("POL-VPN-001", "POL-VPN-001 v1")])
    judge = _StubJudge(0.5, prompt_version="test-judge/v1")

    report = evaluate_retrieval(
        run_fn=lambda _query: result,
        golden_queries=[_Golden("VPN 审批", ["POL-VPN-001"], "policy_query")],
        latency_samples=0,
        judge=judge,
    )

    assert judge.calls == ["VPN 审批"]
    assert report.groundedness == 0.5
    assert report.groundedness_reason == ""
    assert report.groundedness_judge == "deepseek-flash/test-judge/v1"


def test_a_judge_that_cannot_score_leaves_groundedness_unavailable():
    result = _Result(evidence=[_evidence("POL-VPN-001", "POL-VPN-001 v1")])

    report = evaluate_retrieval(
        run_fn=lambda _query: result,
        golden_queries=[_Golden("VPN 审批", ["POL-VPN-001"], "policy_query")],
        latency_samples=0,
        judge=_StubJudge(None),
    )

    assert report.groundedness is None
    assert "did not return a usable score" in report.groundedness_reason


# ------------------------------------------------------------- judge parsing


def test_judge_payload_scoring_is_validated():
    from eval.judge import LLMGroundednessJudge

    assert LLMGroundednessJudge.score_from_payload({"scores": [1.0, 0.5]}, 2) == 0.75
    # Out of range, non-numeric, partial and empty payloads are all "no score".
    assert LLMGroundednessJudge.score_from_payload({"scores": [2.0]}, 1) is None
    assert LLMGroundednessJudge.score_from_payload({"scores": ["high"]}, 1) is None
    assert LLMGroundednessJudge.score_from_payload({"scores": [1.0]}, 3) is None
    assert LLMGroundednessJudge.score_from_payload({"scores": []}, 0) is None
    assert LLMGroundednessJudge.score_from_payload({"reason": "x"}, 1) is None


def test_judge_without_an_api_key_reports_unavailable():
    from app.retrieval.llm import LLMStructuredClient
    from eval.judge import build_llm_judge

    client = LLMStructuredClient(api_key="")
    assert build_llm_judge(client) is None


def test_judge_prompt_records_its_version_and_bounds_the_evidence():
    from eval.judge import MAX_EVIDENCE_ITEMS, PROMPT_VERSION, LLMGroundednessJudge

    judge = LLMGroundednessJudge.__new__(LLMGroundednessJudge)
    judge.prompt_version = PROMPT_VERSION
    evidence = [_evidence(f"DOC-{index}", f"DOC-{index} v1") for index in range(20)]

    prompt = judge.build_user_prompt("query", evidence)

    assert f"EVIDENCE ({MAX_EVIDENCE_ITEMS} items)" in prompt
    assert "DOC-19" not in prompt
    assert judge.prompt_version.startswith("groundedness-judge/")


# ------------------------------------------------------------------ the gate


def test_gate_passes_above_the_thresholds():
    result = evaluate_gate(_report(), build_gate({}))
    assert result.passed is True
    assert result.exit_code == EXIT_OK


def test_gate_fails_below_a_threshold():
    result = evaluate_gate(_report(recall_at_k=0.42), build_gate({}))
    assert result.passed is False
    assert [row.metric for row in result.failures] == ["recall_at_k"]
    assert "below min" in result.failures[0].detail


def test_gate_fails_above_a_maximum():
    gate = build_gate({"p95_latency_ms": {"max": 100.0}})
    result = evaluate_gate(_report(p95_latency_ms=250.0), gate)
    assert result.passed is False
    assert "above max" in result.failures[0].detail


def test_gate_does_not_fail_on_groundedness_by_default():
    """A judge is optional, so an unmeasured groundedness is not a red build."""
    result = evaluate_gate(_report(), build_gate({}))
    assert result.passed is True


def test_requiring_groundedness_fails_it_when_no_judge_ran():
    gate = build_gate({}, require=["groundedness"])
    result = evaluate_gate(_report(), gate, require=["groundedness"])

    assert result.passed is False
    assert result.failures[0].metric == "groundedness"
    assert result.failures[0].status == "FAIL"
    assert "required but unavailable" in result.failures[0].detail


def test_unavailable_policy_can_force_a_failure():
    gate = build_gate({"groundedness": {"unavailable": "fail"}})
    result = evaluate_gate(_report(), gate)
    assert result.passed is False


def test_unavailable_policy_can_be_relaxed():
    gate = build_gate({"groundedness": {"required": True, "unavailable": "skip"}})
    result = evaluate_gate(_report(), gate)
    assert result.passed is True


def test_measured_groundedness_is_gated_normally():
    gate = build_gate({"groundedness": {"min": 0.7}}, require=["groundedness"])
    assert evaluate_gate(_report(groundedness=0.9), gate, require=["groundedness"]).passed
    assert not evaluate_gate(
        _report(groundedness=0.1), gate, require=["groundedness"]
    ).passed


def test_a_metric_without_a_threshold_is_reported_but_not_gated():
    result = evaluate_gate(_report(), build_gate({}))
    row = next(row for row in result.rows if row.metric == "p95_latency_ms")
    assert row.status == "not-gated"
    assert row.value == 5.0


def test_gate_table_names_every_metric():
    table = format_table(evaluate_gate(_report(), build_gate({})).rows)
    assert "recall_at_k" in table
    assert "citation_accuracy" in table
    assert "groundedness" in table
    assert "PASS" in table


def test_unknown_metric_is_a_usage_error():
    with pytest.raises(ValueError):
        build_gate({}, require=["no_such_metric"])
    with pytest.raises(ValueError):
        parse_set_argument("no_such_metric=1.0")
    with pytest.raises(ValueError):
        parse_set_argument("recall_at_k")


@pytest.fixture
def read_thresholds_file(monkeypatch):
    """Serve a thresholds file from memory.

    The suite must not depend on being able to create files: the sandbox it runs
    in may deny that to the test process. `read_text` is the only thing
    `load_thresholds` does with the path, so patching it exercises the real
    parsing without needing a real file.
    """

    def serve(path: Path, content: str) -> Path:
        original = Path.read_text

        def read_text(self, *args, **kwargs):
            if str(self) == str(path):
                return content
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        return path

    return serve


def test_thresholds_file_is_read_and_overlaid(read_thresholds_file):
    path = read_thresholds_file(
        Path("thresholds.json"),
        json.dumps({"recall_at_k": 0.75, "mrr": {"min": 0.5, "required": False}}),
    )

    gate = build_gate(load_thresholds(path))

    assert gate["recall_at_k"].min_value == 0.75
    assert gate["mrr"].min_value == 0.5
    assert gate["ndcg_at_k"].min_value == DEFAULT_THRESHOLDS["ndcg_at_k"]["min"]


def test_thresholds_file_rejects_an_unknown_metric(read_thresholds_file):
    path = read_thresholds_file(
        Path("thresholds.json"), json.dumps({"not_a_metric": 1})
    )
    with pytest.raises(ValueError):
        load_thresholds(path)


def test_thresholds_file_rejects_a_non_object(read_thresholds_file):
    path = read_thresholds_file(Path("thresholds.json"), "[1, 2, 3]")
    with pytest.raises(ValueError):
        load_thresholds(path)


def test_set_overrides_parse_every_supported_field():
    assert parse_set_argument("recall_at_k=0.9") == ("recall_at_k", {"min": 0.9})
    assert parse_set_argument("mrr.max=0.5") == ("mrr", {"max": 0.5})
    assert parse_set_argument("mrr.required=true") == ("mrr", {"required": True})
    assert parse_set_argument("mrr.unavailable=skip") == (
        "mrr",
        {"unavailable": "skip"},
    )


# --------------------------------------------------------------- main() / CLI


def _stub_evaluate(report: EvaluationReport):
    def evaluate(**_kwargs) -> EvaluationReport:
        return report

    return evaluate


def test_main_exits_zero_above_the_threshold(capsys):
    code = main(["--quiet"], evaluate=_stub_evaluate(_report()))

    assert code == EXIT_OK
    assert "gate: PASS" in capsys.readouterr().out


def test_main_exits_non_zero_below_the_threshold(capsys):
    code = main(["--quiet"], evaluate=_stub_evaluate(_report(recall_at_k=0.1)))

    assert code == EXIT_GATE_FAILED
    output = capsys.readouterr().out
    assert "gate: FAIL" in output
    assert "recall_at_k" in output


def test_main_exits_non_zero_when_a_required_metric_is_unavailable():
    """The default run has no judge, so requiring groundedness must fail."""
    code = main(
        ["--quiet", "--require", "groundedness"],
        evaluate=_stub_evaluate(_report()),
    )
    assert code == EXIT_GATE_FAILED


def test_main_exits_zero_when_the_required_metric_was_measured():
    code = main(
        ["--quiet", "--require", "groundedness"],
        evaluate=_stub_evaluate(_report(groundedness=0.9, groundedness_reason="")),
    )
    assert code == EXIT_OK


def test_main_exits_non_zero_on_a_threshold_from_the_command_line():
    failing = main(
        ["--quiet", "--set", "citation_accuracy=0.99"],
        evaluate=_stub_evaluate(_report(citation_accuracy=0.5)),
    )
    passing = main(
        ["--quiet", "--set", "citation_accuracy=0.1"],
        evaluate=_stub_evaluate(_report(citation_accuracy=0.5)),
    )

    assert failing == EXIT_GATE_FAILED
    assert passing == EXIT_OK


def test_main_returns_a_usage_error_for_a_bad_invocation(capsys):
    code = main(["--set", "nonsense=1"], evaluate=_stub_evaluate(_report()))
    assert code == EXIT_USAGE
    assert "configuration error" in capsys.readouterr().err


def test_main_writes_json_output(monkeypatch):
    """The JSON sidecar is written, without the test process needing a disk."""
    written: dict[str, str] = {}

    def write_text(self, content, *args, **kwargs):
        written[str(self)] = content
        return len(content)

    monkeypatch.setattr(Path, "write_text", write_text)

    code = main(
        ["--quiet", "--json", "eval-report.json"],
        evaluate=_stub_evaluate(_report(groundedness=0.9)),
    )

    assert code == EXIT_OK
    assert "eval-report.json" in written
    payload = json.loads(written["eval-report.json"])
    assert payload["gate"]["passed"] is True
    assert payload["report"]["groundedness"] == 0.9


def test_main_reports_a_broken_evaluation_as_a_failure(capsys):
    def explode(**_kwargs):
        raise RuntimeError("retrieval blew up")

    code = main(["--quiet"], evaluate=explode)

    assert code == EXIT_GATE_FAILED
    assert "evaluation failed" in capsys.readouterr().err


def test_main_prints_the_report_unless_quiet(capsys):
    main([], evaluate=_stub_evaluate(_report()))
    output = capsys.readouterr().out
    assert '"recall_at_k"' in output
    assert "groundedness unavailable" in output
