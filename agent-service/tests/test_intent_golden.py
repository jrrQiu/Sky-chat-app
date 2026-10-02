from app.intent.resolver import resolve_intent


GOLDEN_CASES = [
    ("VPN 制度是什么", "network", "query", "network.vpn.policy_query", "low"),
    ("我要在外网访问财务系统，帮我申请 VPN", "network", "submit", "network.vpn.request", "high"),
    ("报销制度是什么", "finance", "query", "finance.policy.query", "low"),
    ("我要提交一笔差旅报销发票", "finance", "submit", "finance.service.submit", "high"),
    ("审批流程是什么", "approval", "query", "approval.policy.query", "low"),
    ("帮我审批这个报销单", "approval", "approve", "approval.decision.request", "high"),
    ("账号被锁了", "it", "submit", "it.service.request", "medium"),
    ("财务系统 VPN 被拒怎么排查", "knowledge", "investigate", "knowledge.ticket.investigation", "low"),
]


def test_golden_intent_cases_do_not_confuse_query_and_write():
    for text, domain, operation, intent, risk in GOLDEN_CASES:
        result = resolve_intent(text)
        assert result.domain == domain, text
        assert result.operation == operation, text
        assert result.intent == intent, text
        assert result.risk == risk, text
