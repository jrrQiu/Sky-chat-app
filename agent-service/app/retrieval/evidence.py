from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.retrieval.query import QueryPlan


@dataclass
class EvidenceGateResult:
    passed: bool
    reason: str
    issues: list[str]


def gate_evidence(
    plan: QueryPlan,
    evidence: list[dict[str, Any]],
    issues: list[str],
) -> EvidenceGateResult:
    policy_evidence = [item for item in evidence if item["source_type"] == "policy"]
    ticket_evidence = [item for item in evidence if item["source_type"] == "ticket"]
    gate_issues = list(issues)

    if plan.task_type in {"policy_query", "cross_kb_query", "mixed"}:
        if not policy_evidence:
            return EvidenceGateResult(
                passed=False,
                reason="当前知识库未检索到有效制度。",
                issues=gate_issues,
            )

    if plan.task_type in {"ticket_investigation", "mixed"}:
        if not ticket_evidence:
            gate_issues.append("未检索到相似已解决工单，输出无匹配结论。")

    active_sources = {
        item["source_id"]
        for item in policy_evidence
        if item.get("metadata", {}).get("status") == "active"
    }
    historical_sources = {
        item["source_id"]
        for item in policy_evidence
        if item.get("metadata", {}).get("status") != "active"
    }
    if active_sources and historical_sources:
        gate_issues.append("检测到新旧制度版本同时出现，当前结论仅以 active 版本为准。")

    return EvidenceGateResult(
        passed=bool(evidence),
        reason="Evidence Gate 通过。" if evidence else "Evidence Gate 未通过。",
        issues=gate_issues,
    )
