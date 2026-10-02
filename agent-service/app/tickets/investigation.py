from __future__ import annotations

from typing import Any

from app.knowledge.models import Evidence
from app.retrieval.query import QueryPlan
from app.retrieval.types import RetrievalIndex, ScoredItem
from app.tickets.signature import build_fault_signature, signature_text
from app.tickets.similar_search import search_similar_tickets, ticket_text
from app.tickets.timeline import format_timeline, get_timeline


_STOP_WORDS = {"怎么", "如何", "排查", "定位", "分析", "被拒", "问题", "工单", "历史"}


def _rewrite_query(query: str, step: int, signature: dict[str, Any]) -> str:
    if step == 1:
        words = [word for word in query.split() if word not in _STOP_WORDS]
        return " ".join(words) or signature.get("symptom", query)
    if step >= 2:
        return signature_text(
            {
                **signature,
                "symptom": " ".join(
                    signature.get("error_codes", []) or [signature.get("product", "")]
                ),
            }
        )
    return query


def _compare_match(
    signature: dict[str, Any],
    ticket: Any,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if signature.get("product") and signature["product"].lower() in ticket.product.lower():
        reasons.append("产品一致")
    if signature.get("component") and signature["component"].lower() in ticket.component.lower():
        reasons.append("组件一致")
    if signature.get("environment") == ticket.environment:
        reasons.append("环境一致")

    current_errors = {code.lower() for code in signature.get("error_codes", [])}
    ticket_errors = {code.lower() for code in ticket.error_codes}
    if current_errors and ticket_errors:
        if current_errors & ticket_errors:
            reasons.append("错误码精确匹配")
        elif reasons:
            reasons.append("错误码未精确匹配")

    if len(reasons) >= 3:
        return "match", reasons
    if reasons:
        return "partial_match", reasons
    return "unmatched", reasons


def _ticket_evidence(
    index: RetrievalIndex,
    ticket: Any,
    candidate: ScoredItem,
    signature: dict[str, Any],
) -> Evidence:
    events = get_timeline(index, ticket.id)
    match_type, reasons = _compare_match(signature, ticket)
    timeline_text = format_timeline(events)
    content = "\n\n".join([ticket_text(ticket), f"时间线：\n{timeline_text}"])
    return Evidence(
        id=f"ev_ticket_{ticket.id}",
        source_type="ticket",
        source_id=ticket.id,
        kb_id="TICKET_CASES",
        title=ticket.symptom,
        content=content,
        metadata={
            "product": ticket.product,
            "component": ticket.component,
            "environment": ticket.environment,
            "status": ticket.status,
            "error_codes": ticket.error_codes,
            "root_cause": ticket.root_cause,
            "resolution": ticket.resolution,
            "closed_at": ticket.closed_at,
            "match_type": match_type,
            "match_reasons": reasons,
            "timeline": timeline_text,
            "citation": f"工单 {ticket.id}",
        },
        score=round(candidate.score, 4),
        matched_by=candidate.matched_by,
    )


def investigate_ticket(
    index: RetrievalIndex,
    plan: QueryPlan,
    query: str,
    user_context: dict[str, Any] | None = None,
) -> tuple[list[Evidence], str, list[dict[str, Any]], dict[str, Any]]:
    signature = build_fault_signature(query, plan)
    tool_events: list[dict[str, Any]] = []
    candidates: list[ScoredItem] = []
    steps = 0
    seen_candidate_ids: set[str] = set()
    rewritten_query = query

    while steps <= plan.max_steps:
        candidates = search_similar_tickets(
            index,
            signature,
            rewritten_query,
            user_context=user_context,
            top_k=3,
        )
        candidate_ids = {candidate.item.id for candidate in candidates}
        tool_call_id = f"ticket_similar_{steps}"
        tool_events.append(
            {
                "type": "tool_call",
                "toolCallId": tool_call_id,
                "name": "ticket.search_similar",
                "args": {
                    "signature": signature,
                    "query": rewritten_query,
                    "top_k": 3,
                },
            }
        )
        tool_events.append(
            {
                "type": "tool_result",
                "toolCallId": tool_call_id,
                "name": "ticket.search_similar",
                "success": bool(candidates),
                "message": (
                    f"相似工单检索命中 {len(candidates)} 条。"
                    if candidates
                    else "未命中相似工单。"
                ),
                "resultCount": len(candidates),
                "sources": [
                    {"title": candidate.item.id, "url": "", "snippet": candidate.item.symptom}
                    for candidate in candidates
                ],
            }
        )

        if candidates:
            if candidate_ids <= seen_candidate_ids:
                break
            seen_candidate_ids = candidate_ids
            break

        steps += 1
        rewritten_query = _rewrite_query(rewritten_query, steps, signature)
        if rewritten_query == query and steps >= plan.max_steps:
            break

    evidence: list[Evidence] = []
    match_groups: dict[str, list[Any]] = {"match": [], "partial_match": [], "unmatched": []}
    for candidate in candidates:
        ticket = candidate.item
        item = _ticket_evidence(index, ticket, candidate, signature)
        evidence.append(item)
        match_groups[str(item.metadata["match_type"])].append(item)

    context_blocks = [
        "【历史工单排查】",
        f"故障签名：{signature_text(signature)}",
    ]
    if not evidence:
        context_blocks.append(
            "当前未检索到相似已解决工单。请补充产品、环境或错误码后重试。"
        )
    else:
        for group_name, group_label in [
            ("match", "匹配"),
            ("partial_match", "部分匹配"),
            ("unmatched", "未匹配"),
        ]:
            for item in match_groups[group_name]:
                context_blocks.append(
                    f"[{group_label}] {item.title}\n"
                    f"引用：{item.metadata.get('citation', item.source_id)}\n"
                    f"匹配原因：{', '.join(item.metadata.get('match_reasons', [])) or '无'}\n"
                    f"{item.content}"
                )

    if evidence:
        root_causes = sorted(
            {item.metadata.get("root_cause", "") for item in evidence if item.metadata.get("root_cause")}
        )
        if root_causes:
            context_blocks.append(
                "根因聚类：\n" + "\n".join(f"- {cause}" for cause in root_causes)
            )
        context_blocks.append(
            "建议下一步：先核对设备编号、证书和 IdP 同步状态；"
            "再按命中工单的解决动作执行，并在系统中记录验证结果。"
        )

    run = {
        "steps": steps,
        "max_steps": plan.max_steps,
        "candidate_ids": [item.source_id for item in evidence],
        "signature": signature,
    }
    return evidence, "\n\n".join(context_blocks), tool_events, run
