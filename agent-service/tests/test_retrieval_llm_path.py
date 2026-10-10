from app.config import settings
from app.retrieval.pipeline import run_retrieval


class FakeQueryClient:
    def complete_json(self, system: str, user: str):
        return {
            "task_type": "policy_query",
            "entities": {},
            "kb_scopes": ["FINANCE_POLICY"],
            "subqueries": [
                {
                    "kb": "FINANCE_POLICY",
                    "query": "差旅报销审批",
                    "filters": {"status": "active"},
                }
            ],
            "evidence_requirements": ["至少一条 active 制度"],
            "max_steps": 0,
        }


class FakeRerankClient:
    def complete_json(self, system: str, user: str):
        return {
            "ranking": [
                "POL-VPN-SOP-001-ROOT",
                "POL-VPN-001-ROOT",
                "POL-APPROVAL-001-ROOT",
            ]
        }


class FakeEmbeddingProvider:
    """Returns vectors at the configured width.

    The width matters now that persisted vectors are searched in pgvector: the query
    vector is cast to `vector(N)`, and a mismatch (`expected 1024 dimensions`) makes
    the store fall back, so a fake of the wrong width would silently stop exercising
    the dense channel instead of failing loudly.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or settings.embedding_dimension

    def embed(self, text: str):
        return [0.1] * self.dimensions


def test_model_query_understanding_is_used_when_client_provided():
    result = run_retrieval(
        "差旅报销超过5000元需要什么审批",
        query_client=FakeQueryClient(),
    )

    assert result.task_type == "policy_query"
    assert result.plan["kb_scopes"] == ["FINANCE_POLICY"]
    # Scoping still holds: only the routed knowledge base contributes. The document
    # may now appear as several chunk-level evidence blocks rather than exactly one,
    # because evidence is no longer collapsed to one block per document — see
    # `test_retrieval_evidence_granularity.py`.
    assert {item["source_id"] for item in result.evidence} == {"POL-FINANCE-001"}
    assert len({item["metadata"]["chunk_id"] for item in result.evidence}) == len(
        result.evidence
    ), "each evidence block must cite a distinct chunk"


def test_model_reranker_can_promote_policy_sop():
    result = run_retrieval(
        "VPN 申请流程有哪些步骤",
        rerank_client=FakeRerankClient(),
    )

    assert result.evidence[0]["source_id"] == "POL-VPN-SOP-001"


def test_dense_embedding_provider_path_is_usable():
    result = run_retrieval(
        "VPN 外网访问财务系统需要哪些审批和条件？",
        embedding_provider=FakeEmbeddingProvider(),
    )

    assert result.evidence
    assert any(item["matched_by"] and "vector" in item["matched_by"] for item in result.evidence)
