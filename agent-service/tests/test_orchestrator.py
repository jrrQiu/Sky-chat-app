from app.graph.nodes.orchestrator import classify_request


def test_classify_vpn_request():
    result = classify_request("我要在外网访问财务系统，帮我申请 VPN")
    assert result["agent_id"] == "network"
    assert result["risk_level"] == "high"
    assert result["intent"] == "network.vpn.request"


def test_classify_general_question():
    result = classify_request("这个流程怎么走？")
    assert result["agent_id"] == "knowledge"
    assert result["risk_level"] == "low"


def test_classify_hr_leave_query():
    result = classify_request("我的年假还有多少天")
    assert result["agent_id"] == "hr"
    assert result["intent"] == "hr.leave.query"
    assert result["action"] == "query"


def test_classify_finance_reimbursement_query():
    result = classify_request("查询我的报销付款状态")
    assert result["agent_id"] == "finance"
    assert result["intent"] == "finance.reimbursement.query"
    assert result["action"] == "query"


def test_classify_approval_request_before_finance():
    result = classify_request("帮我审批这个报销单")
    assert result["agent_id"] == "approval"
    assert result["intent"] == "approval.decision.request"


def test_classify_finance_submit():
    result = classify_request("我要提交一笔差旅报销发票")
    assert result["agent_id"] == "finance"
    assert result["intent"] == "finance.service.submit"


def test_classify_ticket_investigation_routes_knowledge():
    result = classify_request("财务系统 VPN 被拒怎么排查")
    assert result["agent_id"] == "knowledge"
    assert result["intent"] == "knowledge.ticket.investigation"
    assert result["risk_level"] == "low"
