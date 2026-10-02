from __future__ import annotations

from app.knowledge.models import (
    KnowledgeBase,
    PolicyDocument,
    TicketCase,
    TicketEvent,
)


KNOWLEDGE_BASES = [
    KnowledgeBase(
        id="IT_SOP",
        name="IT 操作规范",
        domain="it",
        description="IT 服务、账号、设备、VPN 操作和故障处理流程。",
        keywords=["it", "密码", "解锁", "账号", "软件", "设备", "vpn", "网络", "工单", "故障"],
    ),
    KnowledgeBase(
        id="SECURITY_POLICY",
        name="网络安全与访问制度",
        domain="security",
        description="外网访问、VPN、权限和高风险审批制度。",
        keywords=["vpn", "外网", "防火墙", "内网", "权限", "审批", "网络安全", "敏感系统"],
    ),
    KnowledgeBase(
        id="HR_POLICY",
        name="人事制度",
        domain="hr",
        description="请假、年假、病假和人事证明制度。",
        keywords=["请假", "年假", "病假", "hr", "入职", "证明", "员工"],
    ),
    KnowledgeBase(
        id="FINANCE_POLICY",
        name="财务制度",
        domain="finance",
        description="报销、发票、差旅、预算和付款制度。",
        keywords=["报销", "发票", "差旅", "财务", "付款", "预算", "采购"],
    ),
]


POLICY_DOCUMENTS = [
    PolicyDocument(
        id="POL-VPN-001",
        kb_id="SECURITY_POLICY",
        title="VPN 外网访问制度",
        content=(
            "员工外网访问财务、研发等敏感系统时，必须提交 VPN 申请。"
            "申请需主管和对应系统管理员双重审批。"
            "申请人需填写设备编号、访问目标和有效期。"
            "审批通过后安全管理员按最小权限原则开通账号，默认有效期 90 天。"
        ),
        version="2.0",
        authority_level=100,
        effective_at="2025-01-01",
        expires_at=None,
        status="active",
        keywords=["vpn", "外网", "财务系统", "研发系统", "敏感系统", "审批", "设备编号", "有效期"],
    ),
    PolicyDocument(
        id="POL-VPN-001-ARCHIVED",
        kb_id="SECURITY_POLICY",
        title="VPN 外网访问制度",
        content=(
            "旧版制度：员工外网访问敏感系统仅需主管审批，无需系统管理员复核。"
        ),
        version="1.0",
        authority_level=100,
        effective_at="2024-01-01",
        expires_at="2024-12-31",
        status="archived",
        keywords=["vpn", "外网", "旧制度", "审批"],
    ),
    PolicyDocument(
        id="POL-VPN-SOP-001",
        kb_id="IT_SOP",
        title="VPN 申请操作流程",
        content=(
            "VPN 申请具体步骤如下："
            "1. 申请人填写 VPN 申请单和设备编号；"
            "2. 主管审批；"
            "3. 对应系统管理员审批；"
            "4. 安全管理员按最小权限开通；"
            "5. 申请人验证访问并在台账中登记。"
        ),
        version="1.0",
        authority_level=80,
        effective_at="2025-01-01",
        expires_at=None,
        status="active",
        keywords=["vpn", "申请", "流程", "主管", "系统管理员", "安全管理员", "审批"],
    ),
    PolicyDocument(
        id="POL-PASSWORD-001",
        kb_id="IT_SOP",
        title="密码重置流程",
        content=(
            "员工忘记密码时可通过统一身份平台自助重置。"
            "账号被锁定后，需由 IT 服务台核验身份并解锁。"
            "重置完成后建议等待 IdP 同步，再尝试登录业务系统。"
        ),
        version="1.0",
        authority_level=90,
        effective_at="2025-03-01",
        expires_at=None,
        status="active",
        keywords=["密码", "重置", "解锁", "账号", "idp", "登录", "身份"],
    ),
    PolicyDocument(
        id="POL-LEAVE-001",
        kb_id="HR_POLICY",
        title="请假申请规范",
        content=(
            "年假需提前 3 个工作日申请。"
            "病假需在 24 小时内补交证明。"
            "超过 3 天的请假需要主管和 HRBP 审批。"
        ),
        version="2.0",
        authority_level=90,
        effective_at="2025-01-01",
        expires_at=None,
        status="active",
        keywords=["请假", "年假", "病假", "hrbp", "审批", "证明"],
    ),
    PolicyDocument(
        id="POL-FINANCE-001",
        kb_id="FINANCE_POLICY",
        title="差旅报销规范",
        content=(
            "差旅报销需在结束后 30 天内提交，发票必须真实有效。"
            "单笔超过 5000 元的报销需要部门和财务双重审批。"
        ),
        version="1.0",
        authority_level=90,
        effective_at="2025-01-01",
        expires_at=None,
        status="active",
        keywords=["报销", "发票", "差旅", "财务", "审批", "金额"],
    ),
    PolicyDocument(
        id="POL-APPROVAL-001",
        kb_id="SECURITY_POLICY",
        title="高风险审批阈值规则",
        content=(
            "VPN、财务付款、敏感系统权限变更属于高风险操作，必须保留审批证据。"
        ),
        version="1.0",
        authority_level=95,
        effective_at="2025-01-01",
        expires_at=None,
        status="active",
        keywords=["审批", "vpn", "付款", "权限", "高风险", "敏感系统"],
    ),
]


TICKET_CASES = [
    TicketCase(
        id="TC-1001",
        tenant_id="skychat",
        product="VPN",
        component="security",
        environment="office",
        status="resolved",
        symptom="外网访问财务系统被拒绝",
        error_codes=["ERR-ACCESS-403", "VPN_REJECT"],
        root_cause="VPN 申请单未填写设备编号，被安全管理员退回。",
        resolution="补齐设备编号后重新提交，完成主管和系统管理员双重审批。",
        closed_at="2025-08-12",
        keywords=["vpn", "财务系统", "设备编号", "拒绝", "外网", "审批"],
        recent_change="当日新增财务系统访问申请",
    ),
    TicketCase(
        id="TC-1002",
        tenant_id="skychat",
        product="IdP",
        component="identity",
        environment="office",
        status="resolved",
        symptom="密码重置后无法登录",
        error_codes=["AUTH_FAILED"],
        root_cause="用户密码重置后未等待 IdP 同步完成。",
        resolution="IT 服务台确认 IdP 同步状态后账号恢复正常。",
        closed_at="2025-09-01",
        keywords=["密码", "重置", "登录", "idp", "同步", "账号"],
        recent_change="员工自助重置密码",
    ),
    TicketCase(
        id="TC-1003",
        tenant_id="skychat",
        product="VPN",
        component="network",
        environment="home",
        status="resolved",
        symptom="外网访问财务系统超时",
        error_codes=["TIMEOUT-504"],
        root_cause="未接入企业 VPN，或客户端证书已过期。",
        resolution="重新导入证书并接入企业 VPN，验证连接恢复。",
        closed_at="2025-07-20",
        keywords=["vpn", "财务系统", "超时", "证书", "外网", "连接"],
        recent_change="员工居家办公首次接入",
    ),
    TicketCase(
        id="TC-1004",
        tenant_id="skychat",
        product="ERP",
        component="finance",
        environment="office",
        status="resolved",
        symptom="差旅报销发票被退回",
        error_codes=["INVOICE_INVALID"],
        root_cause="发票抬头与报销人信息不一致。",
        resolution="重新开具正确抬头发票后提交，财务复核通过。",
        closed_at="2025-06-11",
        keywords=["报销", "发票", "退回", "财务", "差旅", "抬头"],
        recent_change="提交差旅报销",
    ),
]


TICKET_EVENTS = [
    TicketEvent(
        id="EV-1001-1",
        case_id="TC-1001",
        event_type="created",
        content="用户提交外网访问财务系统的 VPN 申请。",
        actor_hash="user:a1",
        occurred_at="2025-08-11T09:10:00+08:00",
    ),
    TicketEvent(
        id="EV-1001-2",
        case_id="TC-1001",
        event_type="rejected",
        content="安全管理员退回：缺少设备编号。",
        actor_hash="admin:s1",
        occurred_at="2025-08-11T10:30:00+08:00",
    ),
    TicketEvent(
        id="EV-1001-3",
        case_id="TC-1001",
        event_type="resubmitted",
        content="用户补齐设备编号并重新提交。",
        actor_hash="user:a1",
        occurred_at="2025-08-12T09:00:00+08:00",
    ),
    TicketEvent(
        id="EV-1001-4",
        case_id="TC-1001",
        event_type="approved",
        content="主管与系统管理员完成双重审批。",
        actor_hash="mgr:m1",
        occurred_at="2025-08-12T10:00:00+08:00",
    ),
    TicketEvent(
        id="EV-1001-5",
        case_id="TC-1001",
        event_type="resolved",
        content="VPN 权限已按最小权限原则开通。",
        actor_hash="admin:s1",
        occurred_at="2025-08-12T11:20:00+08:00",
    ),
    TicketEvent(
        id="EV-1002-1",
        case_id="TC-1002",
        event_type="created",
        content="用户报告密码重置后无法登录。",
        actor_hash="user:b1",
        occurred_at="2025-09-01T08:00:00+08:00",
    ),
    TicketEvent(
        id="EV-1002-2",
        case_id="TC-1002",
        event_type="resolved",
        content="确认 IdP 同步完成后账号恢复正常。",
        actor_hash="it:u1",
        occurred_at="2025-09-01T08:45:00+08:00",
    ),
    TicketEvent(
        id="EV-1003-1",
        case_id="TC-1003",
        event_type="created",
        content="用户报告外网访问财务系统超时。",
        actor_hash="user:c1",
        occurred_at="2025-07-20T14:00:00+08:00",
    ),
    TicketEvent(
        id="EV-1003-2",
        case_id="TC-1003",
        event_type="resolved",
        content="重新导入客户端证书后连接恢复。",
        actor_hash="it:u1",
        occurred_at="2025-07-20T15:10:00+08:00",
    ),
]
