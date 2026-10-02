from eval.metrics import evaluate_retrieval


def test_evaluation_meets_retrieval_baselines():
    report = evaluate_retrieval(latency_samples=5)

    assert report.recall_at_k >= 0.9
    assert report.mrr >= 0.8
    assert report.ndcg_at_k >= 0.8
    assert report.precision_at_k >= 0.5
    assert report.citation_accuracy == 1.0
    assert report.groundedness == 1.0
    assert report.p95_latency_ms >= 0
