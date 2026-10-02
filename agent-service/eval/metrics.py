from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from app.retrieval.pipeline import run_retrieval
from eval.golden_set import GOLDEN_QUERIES


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


@dataclass
class EvaluationReport:
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    precision_at_k: float
    citation_accuracy: float
    groundedness: float
    p95_latency_ms: float
    query_details: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(0.95 * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def evaluate_retrieval(
    run_fn: Callable[..., Any] = run_retrieval,
    golden_queries: list[Any] = GOLDEN_QUERIES,
    k: int = 5,
    latency_samples: int = 30,
) -> EvaluationReport:
    recall_scores: list[float] = []
    mrr_scores: list[float] = []
    ndcg_scores: list[float] = []
    precision_scores: list[float] = []
    citation_scores: list[float] = []
    grounded_scores: list[float] = []
    details: list[dict[str, Any]] = []
    latencies: list[float] = []

    for golden in golden_queries:
        result = run_fn(golden.query)
        retrieved = [item["source_id"] for item in result.evidence]
        relevant = set(golden.relevant)

        recall_scores.append(recall_at_k(relevant, retrieved, k))
        mrr_scores.append(mean_reciprocal_rank(relevant, retrieved))
        ndcg_scores.append(ndcg_at_k(relevant, retrieved, k))
        precision_scores.append(precision_at_k(relevant, retrieved, k))

        citation_scores.append(
            statistics.mean(
                [
                    1.0 if item.get("source_id") and item.get("metadata", {}).get("citation") else 0.0
                    for item in result.evidence
                ]
            )
            if result.evidence
            else 0.0
        )
        grounded_scores.append(
            statistics.mean(
                [
                    1.0 if item.get("source_id") and item.get("content", "").strip() else 0.0
                    for item in result.evidence
                ]
            )
            if result.evidence
            else 0.0
        )

        details.append(
            {
                "query": golden.query,
                "task_type": result.task_type,
                "retrieved": retrieved,
                "relevant": sorted(relevant),
                "recall_at_k": recall_scores[-1],
                "mrr": mrr_scores[-1],
            }
        )

        for _ in range(latency_samples):
            started = time.perf_counter()
            run_fn(golden.query)
            latencies.append((time.perf_counter() - started) * 1000)

    return EvaluationReport(
        recall_at_k=statistics.mean(recall_scores),
        mrr=statistics.mean(mrr_scores),
        ndcg_at_k=statistics.mean(ndcg_scores),
        precision_at_k=statistics.mean(precision_scores),
        citation_accuracy=statistics.mean(citation_scores),
        groundedness=statistics.mean(grounded_scores),
        p95_latency_ms=_p95(latencies),
        query_details=details,
    )
