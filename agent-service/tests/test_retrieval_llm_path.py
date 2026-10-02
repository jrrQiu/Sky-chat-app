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
    def embed(self, text: str):
        return [0.1] * 8


def test_model_query_understanding_is_used_when_client_provided():
    result = run_retrieval(
        "差旅报销超过5000元需要什么审批",
        query_client=FakeQueryClient(),
    )

    assert result.task_type == "policy_query"
    assert result.plan["kb_scopes"] == ["FINANCE_POLICY"]
    assert [item["source_id"] for item in result.evidence] == ["POL-FINANCE-001"]


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
