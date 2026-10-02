from app.intent.resolver import resolve_intent
from app.graph.nodes.orchestrator import classify_request


def test_vpn_policy_is_not_vpn_submit():
    result = resolve_intent("VPN 制度是什么")
    assert result.domain == "network"
    assert result.operation == "query"
    assert result.intent == "network.vpn.policy_query"
    assert result.risk == "low"
    assert result.agent_id == "knowledge"


def test_finance_policy_is_not_finance_submit():
    result = classify_request("报销制度是什么")
    assert result["agent_id"] == "knowledge"
    assert result["intent"] == "finance.policy.query"
    assert result["operation"] == "query"
    assert result["risk_level"] == "low"


def test_approval_flow_is_query_not_approve():
    result = classify_request("审批流程是什么")
    assert result["agent_id"] == "knowledge"
    assert result["intent"] == "approval.policy.query"
    assert result["operation"] == "query"
    assert result["action"] == "query"


def test_locked_account_routes_to_it():
    result = classify_request("账号被锁了")
    assert result["agent_id"] == "it"
    assert result["intent"] == "it.service.request"
    assert result["risk_level"] == "medium"


def test_ambiguous_request_falls_back_to_knowledge():
    result = resolve_intent("帮我处理一下")
    assert result.agent_id == "knowledge"
    assert result.operation == "query"
    assert result.confidence < 0.60


def test_llm_fallback_is_used_when_rule_confidence_is_low():
    class FakeIntentLLM:
        def complete_json(self, system: str, user: str):
            return {
                "domain": "finance",
                "operation": "query",
                "intent": "finance.reimbursement.query",
                "confidence": 0.95,
                "entities": {"product": "报销"},
            }

    result = resolve_intent("帮我处理一下", llm_client=FakeIntentLLM())

    assert result.domain == "finance"
    assert result.intent == "finance.reimbursement.query"
    assert result.source == "llm"
    assert result.confidence == 0.95
