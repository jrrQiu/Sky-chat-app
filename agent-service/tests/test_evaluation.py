"""Baseline retrieval evaluation.

The IR metrics are unchanged and stay asserted here. `groundedness` is no longer
asserted to be 1.0: measuring it requires an LLM judge, and substituting a
placeholder for a measurement is exactly the defect this test used to hide. It is
now asserted to be *unavailable* instead, and the judge is exercised in
`test_eval_gate.py`.
"""

from eval.metrics import evaluate_retrieval


def test_evaluation_meets_retrieval_baselines():
    report = evaluate_retrieval(latency_samples=5)

    assert report.recall_at_k >= 0.9
    assert report.mrr >= 0.8
    assert report.ndcg_at_k >= 0.8
    assert report.precision_at_k >= 0.5
    # Every citation in the golden corpus resolves to a genuinely retrieved id.
    assert report.citation_accuracy == 1.0
    assert report.citation_details == []
    assert report.p95_latency_ms >= 0


def test_groundedness_is_unavailable_without_a_judge_not_fabricated():
    report = evaluate_retrieval(latency_samples=0)

    assert report.groundedness is None
    assert report.groundedness_reason
    assert report.groundedness_judge == ""
    assert report.to_dict()["groundedness"] is None
