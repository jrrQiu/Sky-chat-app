"""Retrieval evaluation metrics.

Two metrics in this module used to be pseudo-metrics: `citation_accuracy` and
`groundedness` both collapsed to "is this field non-empty", which measures
nothing and always reports 1.0 on a healthy-looking corpus. That is worse than
no metric, because it passes CI. They are replaced as follows.

**citation_accuracy — now deterministic and real.** A citation counts as correct
only when it actually *resolves*: the cited id must be present in the evidence
that was genuinely retrieved for that query, and the rendered citation text must
name that id. A citation pointing at an id that was never retrieved scores 0. An
evidence item with no citation at all scores 0, because an answer built on it
cannot be traced. This is a factual check with no model involved, so it is safe
to gate CI on it.

**groundedness — now an optional LLM judge, never a placeholder.** Deciding
whether retrieved evidence actually supports the query is a judgement call that
no formula can make, so the only honest options are "ask a judge" or "report
unavailable". When no judge is supplied the report carries
``groundedness=None`` plus an explicit ``groundedness_reason``; it is never
substituted with 1.0. When a judge is supplied it is called with temperature 0
and a recorded prompt version, and its token cost is recorded in the metrics
registry like any other model call.

The genuine IR metrics — precision@k, recall@k, MRR, nDCG@k — are unchanged.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from app.observability.registry import metrics
from app.retrieval.pipeline import run_retrieval
from eval.golden_set import GOLDEN_QUERIES

#: Why `groundedness` is missing, when it is.
REASON_NO_JUDGE = (
    "groundedness requires an LLM judge; none was configured, and a "
    "placeholder would be a fabricated measurement"
)
REASON_JUDGE_FAILED = "the LLM judge did not return a usable score"
REASON_NO_EVIDENCE = "no evidence was retrieved for any query"

METRIC_EVAL_CITATIONS = "eval.citation_checks"
HELP_EVAL_CITATIONS = "Citation resolution checks, by result."


def precision_at_k(relevant: set[str], retrieved: list[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return len([item for item in top if item in relevant]) / len(top)


def recall_at_k(relevant: set[str], retrieved: list[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len([item for item in retrieved[:k] if item in relevant]) / len(relevant)


def mean_reciprocal_rank(relevant: set[str], retrieved: list[str]) -> float:
    for index, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(relevant: set[str], retrieved: list[str], k: int) -> float:
    top = retrieved[:k]
    if not relevant:
        return 1.0

    gains = [1.0 if item in relevant else 0.0 for item in top]
    discounted_gain = sum(
        gain / math.log2(index + 1) for index, gain in enumerate(gains, start=1)
    )
    ideal_gains = sorted([1.0] * min(len(relevant), k) + [0.0] * max(k - len(relevant), 0), reverse=True)
    ideal_discounted_gain = sum(
        gain / math.log2(index + 1)
        for index, gain in enumerate(ideal_gains, start=1)
    )
    if ideal_discounted_gain == 0:
        return 0.0
    return discounted_gain / ideal_discounted_gain


# --------------------------------------------------------------- citations


@dataclass(frozen=True)
class CitationCheck:
    """Whether one evidence item carries a citation that resolves."""

    source_id: str
    citation: str
    resolved: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_citation(
    source_id: str,
    citation: str,
    retrieved_ids: Iterable[str],
) -> CitationCheck:
    """Decide whether `citation` genuinely points at retrieved evidence.

    Resolution requires all three of:

    1. the item names a source id,
    2. that id is one of the ids retrieved for this query,
    3. the rendered citation text names that id, so the citation a reader sees
       actually takes them to the retrieved item.

    Checking (3) against the retrieved id — rather than merely checking the
    field is non-empty — is what turns this from a presence check into a
    resolution check: ``POL-HR-999 v1`` on evidence ``POL-VPN-001`` fails.
    """
    candidates = [str(item) for item in retrieved_ids if str(item)]
    cited_id = str(source_id or "").strip()
    rendered = " ".join(str(citation or "").split())

    if not cited_id:
        return CitationCheck("", rendered, False, "evidence has no source id")
    if not rendered:
        return CitationCheck(cited_id, "", False, "citation text is missing")
    if cited_id not in candidates:
        return CitationCheck(
            cited_id, rendered, False, "source id was not retrieved for this query"
        )

    # Longest match first: `TC-1001-A` must not be satisfied by `TC-1001`.
    ordered = sorted((item for item in candidates if item in rendered), key=len, reverse=True)
    if not ordered:
        return CitationCheck(
            cited_id, rendered, False, f"citation text does not name {cited_id}"
        )
    if ordered[0] != cited_id:
        return CitationCheck(
            cited_id,
            rendered,
            False,
            f"citation text names {ordered[0]} instead of {cited_id}",
        )
    return CitationCheck(cited_id, rendered, True, "resolved")


#: How the answer text is expected to mark a citation: `[3]`.
_ANSWER_MARKER_PATTERN = re.compile(r"\[(\d{1,3})\]")


def _answer_markers(answer: str) -> list[int]:
    return [int(match) for match in _ANSWER_MARKER_PATTERN.findall(answer or "")]


def citation_accuracy_for_query(
    evidence: Sequence[dict[str, Any]],
    retrieved_ids: Sequence[str],
    *,
    answer: str | None = None,
) -> tuple[float, list[CitationCheck]]:
    """Score one query's citations.

    Evidence citations are graded individually and averaged. When an `answer` is
    available its ``[n]`` markers are graded too. A marker that points at an
    evidence item does not add a second credit for that item — it is the same
    citation — but a marker pointing past the end of the evidence is an
    unresolvable citation and adds a zero, exactly like a wrong id.

    With no evidence and no answer there is nothing that could have been
    cited, so the query scores 1.0 rather than being punished for an empty
    corpus; a query that retrieved nothing is already penalised by recall@k.
    """
    checks = [
        resolve_citation(
            str(item.get("source_id") or ""),
            str(item.get("metadata", {}).get("citation") or ""),
            retrieved_ids,
        )
        for item in evidence
    ]
    scores = [1.0 if check.resolved else 0.0 for check in checks]

    for marker in _answer_markers(answer or ""):
        if 1 <= marker <= len(evidence):
            continue  # already graded as the evidence item at that index
        checks.append(
            CitationCheck(
                source_id="",
                citation=f"[{marker}]",
                resolved=False,
                reason="answer cites a reference that was not retrieved",
            )
        )
        scores.append(0.0)

    if not scores:
        return 1.0, checks
    return statistics.mean(scores), checks


# --------------------------------------------------------------- groundedness

#: The judge protocol. Returning `None` means "could not judge", which the
#: report records as unavailable rather than as a score.
GroundednessJudge = Callable[[str, Sequence[dict[str, Any]]], "float | None"]


def judge_availability_reason(judge: GroundednessJudge | None) -> str:
    """Human-readable reason to attach when groundedness is unavailable."""
    if judge is None:
        return REASON_NO_JUDGE
    return ""


@dataclass
class EvaluationReport:
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    precision_at_k: float
    citation_accuracy: float
    #: `None` means "not measured", never "measured as zero".
    groundedness: float | None
    p95_latency_ms: float
    query_details: list[dict[str, Any]]
    #: Why `groundedness` is `None`; empty when it was measured.
    groundedness_reason: str = ""
    #: Which judge produced `groundedness`, with its prompt version.
    groundedness_judge: str = ""
    #: Per-item citation resolution detail for the failing cases.
    citation_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def citation_failures(self) -> list[dict[str, Any]]:
        return [item for item in self.citation_details if not item["resolved"]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _judge_name(judge: GroundednessJudge | None) -> str:
    if judge is None:
        return ""
    version = getattr(judge, "prompt_version", "")
    model = getattr(judge, "model", "")
    parts = [part for part in (model, version) if part]
    return "/".join(parts) or type(judge).__name__


def evaluate_retrieval(
    run_fn: Callable[..., Any] = run_retrieval,
    golden_queries: list[Any] = GOLDEN_QUERIES,
    k: int = 5,
    latency_samples: int = 30,
    *,
    judge: GroundednessJudge | None = None,
    answer_fn: Callable[[Any, Any], str] | None = None,
) -> EvaluationReport:
    """Evaluate retrieval over the golden set.

    `judge` is optional and defaults to `None`, which is the honest default: no
    judge means `groundedness` is reported as unavailable. Pass
    ``eval.judge.build_llm_judge()`` to measure it for real.

    `answer_fn` is an optional hook returning the generated answer for a query
    so its ``[n]`` citation markers can be graded as well as the evidence.
    """
    recall_scores: list[float] = []
    mrr_scores: list[float] = []
    ndcg_scores: list[float] = []
    precision_scores: list[float] = []
    citation_scores: list[float] = []
    grounded_scores: list[float] = []
    details: list[dict[str, Any]] = []
    citation_details: list[dict[str, Any]] = []
    latencies: list[float] = []
    judge_requested = judge is not None
    evidence_items_seen = 0

    for golden in golden_queries:
        result = run_fn(golden.query)
        retrieved = [item["source_id"] for item in result.evidence]
        relevant = set(golden.relevant)
        evidence_items_seen += len(result.evidence)

        recall_scores.append(recall_at_k(relevant, retrieved, k))
        mrr_scores.append(mean_reciprocal_rank(relevant, retrieved))
        ndcg_scores.append(ndcg_at_k(relevant, retrieved, k))
        precision_scores.append(precision_at_k(relevant, retrieved, k))

        answer = answer_fn(golden, result) if answer_fn is not None else None
        query_score, checks = citation_accuracy_for_query(
            result.evidence, retrieved, answer=answer
        )
        citation_scores.append(query_score)
        for check in checks:
            if check.resolved:
                metrics.counter(
                    METRIC_EVAL_CITATIONS,
                    HELP_EVAL_CITATIONS,
                    result="resolved",
                ).inc()
            else:
                metrics.counter(
                    METRIC_EVAL_CITATIONS,
                    HELP_EVAL_CITATIONS,
                    result="unresolved",
                ).inc()
                citation_details.append(
                    {"query": golden.query, **check.to_dict()}
                )

        if judge is not None:
            judged = judge(golden.query, result.evidence)
            if judged is not None:
                grounded_scores.append(float(judged))

        details.append(
            {
                "query": golden.query,
                "task_type": result.task_type,
                "retrieved": retrieved,
                "relevant": sorted(relevant),
                "recall_at_k": recall_scores[-1],
                "mrr": mrr_scores[-1],
                "citation_accuracy": citation_scores[-1],
            }
        )

        for _ in range(latency_samples):
            started = time.perf_counter()
            run_fn(golden.query)
            latencies.append((time.perf_counter() - started) * 1000)

    groundedness: float | None = None
    groundedness_reason = judge_availability_reason(judge)
    if judge is not None:
        if grounded_scores:
            groundedness = statistics.mean(grounded_scores)
            groundedness_reason = ""
        elif evidence_items_seen == 0:
            groundedness_reason = REASON_NO_EVIDENCE
        else:
            groundedness_reason = REASON_JUDGE_FAILED

    return EvaluationReport(
        recall_at_k=statistics.mean(recall_scores),
        mrr=statistics.mean(mrr_scores),
        ndcg_at_k=statistics.mean(ndcg_scores),
        precision_at_k=statistics.mean(precision_scores),
        citation_accuracy=statistics.mean(citation_scores),
        groundedness=groundedness,
        p95_latency_ms=_p95(latencies),
        query_details=details,
        groundedness_reason=groundedness_reason,
        groundedness_judge=_judge_name(judge) if judge_requested else "",
        citation_details=citation_details,
    )
