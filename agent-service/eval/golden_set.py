from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenQuery:
    query: str
    relevant: list[str]
    task_type: str


GOLDEN_QUERIES: list[GoldenQuery] = [
    GoldenQuery(
        query="VPN 外网访问财务系统需要哪些审批和条件？",
        relevant=["POL-VPN-001", "POL-VPN-SOP-001"],
        task_type="cross_kb_query",
    ),
    GoldenQuery(
        query="财务系统 VPN 被拒怎么排查",
        relevant=["TC-1001", "TC-1003"],
        task_type="ticket_investigation",
    ),
    GoldenQuery(
        query="差旅报销超过5000元需要什么审批",
        relevant=["POL-FINANCE-001"],
        task_type="policy_query",
    ),
    GoldenQuery(
        query="密码重置流程和历史故障案例",
        relevant=["TC-1002", "POL-PASSWORD-001"],
        task_type="mixed",
    ),
    GoldenQuery(
        query="VPN 申请流程有哪些步骤",
        relevant=["POL-VPN-SOP-001"],
        task_type="policy_query",
    ),
]
