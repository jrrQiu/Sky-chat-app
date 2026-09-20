from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    content: str
    keywords: list[str]
    allowed_roles: list[str] = field(default_factory=list)


DOCUMENTS: list[KnowledgeDocument] = [
    KnowledgeDocument(
        id="KB-VPN-001",
        title="VPN 申请制度",
        content="员工外网访问财务、研发等敏感系统时，必须提交 VPN 申请。需要主管和对应系统管理员双重审批。",
        keywords=["vpn", "外网", "财务系统", "网络", "审批", "访问"],
    ),
    KnowledgeDocument(
        id="KB-IT-001",
        title="密码重置流程",
        content="员工忘记密码时，可通过统一身份平台自助重置。账号被锁定后，需要 IT 服务台核验身份并解锁。",
        keywords=["密码", "重置", "解锁", "账号", "身份", "it"],
    ),
    KnowledgeDocument(
        id="KB-HR-001",
        title="请假申请规范",
        content="年假需提前 3 个工作日申请，病假需在 24 小时内补交证明。超过 3 天的请假需要主管和 HRBP 审批。",
        keywords=["请假", "年假", "病假", "hr", "审批", "证明"],
    ),
    KnowledgeDocument(
        id="KB-FIN-001",
        title="差旅报销规范",
        content="差旅报销需在结束后 30 天内提交，发票必须真实有效。单笔超过 5000 元的报销需要部门和财务双重审批。",
        keywords=["报销", "发票", "差旅", "财务", "审批", "金额"],
    ),
    KnowledgeDocument(
        id="KB-APPROVAL-001",
        title="审批阈值规则",
        content="VPN、财务付款、敏感系统权限变更属于高风险操作，必须保留审批证据。",
        keywords=["审批", "vpn", "付款", "权限", "规则"],
    ),
]

HISTORICAL_TICKETS: list[KnowledgeDocument] = [
    KnowledgeDocument(
        id="TICKET-1001",
        title="历史工单：外网访问财务系统被拒绝",
        content="用户未填写设备信息，VPN 申请被安全管理员退回。补齐设备编号后重新提交并完成双人审批。",
        keywords=["vpn", "财务系统", "设备", "拒绝"],
    ),
    KnowledgeDocument(
        id="TICKET-1002",
        title="历史工单：密码重置后无法登录",
        content="用户密码重置后未等待同步完成，IT 服务台确认 IdP 同步状态后恢复正常。",
        keywords=["密码", "登录", "it", "账号"],
    ),
]


def _score(document: KnowledgeDocument, query: str) -> int:
    normalized = query.lower()
    title_matches = sum(1 for keyword in document.keywords if keyword in normalized)
    content_matches = sum(document.content.lower().count(keyword) for keyword in document.keywords)
    return title_matches * 3 + content_matches


def _can_access(
    document: KnowledgeDocument,
    user_context: dict[str, Any] | None,
) -> bool:
    if not document.allowed_roles:
        return True
    if not user_context:
        return False
    roles = set(user_context.get("roles", []))
    return bool(roles.intersection(document.allowed_roles))


def search_knowledge(
    query: str,
    user_context: dict[str, Any] | None = None,
    limit: int = 3,
) -> list[KnowledgeDocument]:
    scored = [
        (document, _score(document, query))
        for document in DOCUMENTS
        if _score(document, query) > 0
        and _can_access(document, user_context)
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [document for document, _ in scored[:limit]]


def search_historical_tickets(
    query: str,
    user_context: dict[str, Any] | None = None,
    limit: int = 2,
) -> list[KnowledgeDocument]:
    scored = [
        (document, _score(document, query))
        for document in HISTORICAL_TICKETS
        if _score(document, query) > 0
        and _can_access(document, user_context)
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [document for document, _ in scored[:limit]]


def format_knowledge_context(
    query: str,
    include_history: bool = False,
    user_context: dict[str, Any] | None = None,
) -> str:
    knowledge = search_knowledge(query, user_context=user_context)
    history = (
        search_historical_tickets(query, user_context=user_context)
        if include_history
        else []
    )

    if not knowledge and not history:
        return "未检索到完全匹配的制度或历史工单。"

    blocks: list[str] = []
    if knowledge:
        blocks.append("【制度与 SOP】")
        for index, document in enumerate(knowledge, start=1):
            blocks.append(f"[{index}] {document.title}\n{document.content}")

    if history:
        blocks.append("【历史工单】")
        for index, document in enumerate(history, start=1):
            blocks.append(f"[H{index}] {document.title}\n{document.content}")

    return "\n".join(blocks)
