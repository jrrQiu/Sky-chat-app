from app.retrieval.pipeline import run_retrieval


def test_policy_query_filters_archived_version():
    result = run_retrieval("VPN 外网访问财务系统需要哪些审批和条件？")

    source_ids = {item["source_id"] for item in result.evidence}
    assert "POL-VPN-001" in source_ids
    assert "POL-VPN-001-ARCHIVED" not in source_ids

    vpn = next(item for item in result.evidence if item["source_id"] == "POL-VPN-001")
    assert vpn["metadata"]["version"] == "2.0"
    assert vpn["metadata"]["status"] == "active"
    assert any("2.0" in issue for issue in result.issues)


def test_cross_kb_query_routes_multiple_scopes():
    result = run_retrieval("VPN 申请制度和财务报销规范有什么区别")

    assert result.task_type == "cross_kb_query"
    kb_ids = {item["kb_id"] for item in result.evidence}
    assert {"SECURITY_POLICY", "FINANCE_POLICY"} <= kb_ids
    assert "POL-FINANCE-001" in {item["source_id"] for item in result.evidence}


def test_ticket_investigation_returns_timeline_and_root_cause():
    result = run_retrieval("财务系统 VPN 被拒怎么排查")

    assert result.task_type == "ticket_investigation"
    ticket_ids = {item["source_id"] for item in result.evidence}
    assert "TC-1001" in ticket_ids

    ticket = next(item for item in result.evidence if item["source_id"] == "TC-1001")
    assert ticket["metadata"]["timeline"]
    assert ticket["metadata"]["root_cause"]
    assert "根因聚类" in result.context
    assert result.plan["max_steps"] == 4


def test_mixed_query_returns_policy_and_ticket_evidence():
    result = run_retrieval("VPN 制度以及历史故障案例")

    assert result.task_type == "mixed"
    source_types = {item["source_type"] for item in result.evidence}
    assert {"policy", "ticket"} <= source_types
