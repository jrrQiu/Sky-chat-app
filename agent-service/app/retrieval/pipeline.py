from __future__ import annotations

from typing import Any

from app.config import settings
from app.core.guardrails import apply_retrieval_rail
from app.knowledge.ingest import build_seed_index
from app.knowledge.policy_search import retrieve_policy_evidence
from app.persistence.knowledge_repository import PostgresKnowledgeRepository
from app.retrieval.evidence import gate_evidence
from app.retrieval.query import QueryPlan, understand_query, understand_query_with_model
from app.retrieval.router import route_knowledge_bases
from app.retrieval.types import RetrievalIndex, RetrievalResult
from app.tickets.investigation import investigate_ticket


def _kb_keywords(index: RetrievalIndex) -> dict[str, list[str]]:
    return {kb.id: kb.keywords for kb in index.knowledge_bases}


def _policy_tool_call(
    task_type: str,
    kb_ids: list[str],
) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "toolCallId": "policy_hybrid_search",
        "name": "policy.hybrid_search",
        "args": {
            "task_type": task_type,
            "kb_scopes": kb_ids,
            "filters": {"status": "active"},
        },
    }


def _router_tool_events(
    plan: QueryPlan,
    kb_ids: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_call",
            "toolCallId": "retrieval_router",
            "name": "knowledge.router",
            "args": {
                "task_type": plan.task_type,
                "kb_scopes": plan.kb_scopes,
                "selected_kb_scopes": kb_ids,
            },
        },
        {
            "type": "tool_result",
            "toolCallId": "retrieval_router",
            "name": "knowledge.router",
            "success": bool(kb_ids),
            "message": f"已路由到 {len(kb_ids)} 个知识库：{', '.join(kb_ids)}",
            "resultCount": len(kb_ids),
            "sources": [{"title": kb_id, "url": ""} for kb_id in kb_ids],
        },
    ]


def _format_context(
    plan: QueryPlan,
    policy_evidence: list[dict[str, Any]],
    ticket_context: str,
    issues: list[str],
    gate_reason: str,
) -> str:
    blocks: list[str] = []
    if policy_evidence:
        blocks.append("【制度与 SOP】")
        for index, item in enumerate(policy_evidence, start=1):
            metadata = item.get("metadata", {})
            citation = metadata.get("citation", item["source_id"])
            blocks.append(
                f"[{index}] {item['title']}（{citation}）\n"
                f"权威等级：{metadata.get('authority_level', '-')}；"
                f"生效时间：{metadata.get('effective_at', '-')}；"
                f"状态：{metadata.get('status', '-')}\n"
                f"{item['content']}"
            )

    if ticket_context:
        blocks.append(ticket_context)

    if issues:
        blocks.append("【版本与冲突提示】")
        blocks.extend(f"- {issue}" for issue in issues)

    if not policy_evidence and not ticket_context:
        blocks.append(gate_reason)

    return "\n\n".join(blocks)


def _documents_from_evidence(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    documents = []
    for item in evidence:
        documents.append(
            {
                "id": item["source_id"],
                "title": item["title"],
                "content": item["content"],
                "type": item["source_type"],
                "metadata": item.get("metadata", {}),
                "score": item.get("score", 0.0),
            }
        )
    return documents


def run_retrieval(
    query: str,
    user_context: dict[str, Any] | None = None,
    query_client=None,
    rerank_client=None,
    request_id: str | None = None,
    embedding_provider=None,
) -> RetrievalResult:
    # Scope the index to the caller's roles when we have them, so the ACL filter
    # runs in SQL and a revoked permission takes effect on the next query rather
    # than after a process restart. The per-document `_can_access_*` checks stay
    # as defence in depth.
    role_scope = frozenset((user_context or {}).get("roles", [])) if user_context else None
    index = build_seed_index(role_scope)
    kb_keywords = _kb_keywords(index)
    if query_client is not None:
        plan = understand_query_with_model(
            query,
            kb_keywords,
            client=query_client,
        )
    else:
        plan = understand_query(query, kb_keywords)
    kb_ids = route_knowledge_bases(plan, index, user_context)

    policy_evidence: list[Any] = []
    policy_issues: list[str] = []
    policy_events: list[dict[str, Any]] = []
    if plan.task_type in {"policy_query", "cross_kb_query", "mixed"} and kb_ids:
        policy_evidence, policy_issues, policy_events = retrieve_policy_evidence(
            index,
            query,
            kb_ids,
            user_context=user_context,
            rerank_client=rerank_client,
            embedding_provider=embedding_provider,
        )

    ticket_evidence: list[Any] = []
    ticket_context = ""
    ticket_events: list[dict[str, Any]] = []
    ticket_run: dict[str, Any] = {}
    if plan.task_type in {"ticket_investigation", "mixed"}:
        ticket_evidence, ticket_context, ticket_events, ticket_run = investigate_ticket(
            index,
            plan,
            query,
            user_context=user_context,
        )

    evidence = [
        *[item.to_dict() for item in policy_evidence],
        *[item.to_dict() for item in ticket_evidence],
    ]
    # Retrieval rail: retrieved text is untrusted input. Screen it before it can
    # reach the model prompt — this is the indirect prompt-injection boundary.
    evidence, rail_blocked = apply_retrieval_rail(evidence)
    issues = [*policy_issues]
    if rail_blocked:
        issues.append(
            f"检索护栏隔离了 {len(rail_blocked)} 条可疑内容（疑似间接提示注入）。"
        )
    gate = gate_evidence(plan, evidence, issues)

    tool_events: list[dict[str, Any]] = []
    if plan.task_type in {"policy_query", "cross_kb_query", "mixed"}:
        tool_events.extend(_router_tool_events(plan, kb_ids))
        tool_events.append(_policy_tool_call(plan.task_type, kb_ids))
        tool_events.extend(policy_events)
    if plan.task_type in {"ticket_investigation", "mixed"}:
        tool_events.extend(ticket_events)

    plan_payload = plan.to_dict()
    plan_payload["routed_kb_scopes"] = kb_ids
    plan_payload["retrieval_run"] = (
        {
            "task_type": plan.task_type,
            "steps": ticket_run.get("steps", 0),
            "max_steps": ticket_run.get("max_steps", 0),
            "candidate_ids": ticket_run.get("candidate_ids", []),
        }
        if ticket_run
        else {
            "task_type": plan.task_type,
            "steps": 0,
            "max_steps": 0,
            "candidate_ids": [item["source_id"] for item in evidence],
        }
    )

    context = _format_context(
        plan,
        [item for item in evidence if item["source_type"] == "policy"],
        ticket_context,
        gate.issues,
        gate.reason,
    )

    if request_id and settings.retrieval_use_postgres:
        repository = PostgresKnowledgeRepository()
        if repository.is_available():
            repository.record_retrieval_run(
                request_id=request_id,
                task_type=plan.task_type,
                plan=plan_payload,
                candidate_ids=[item["source_id"] for item in evidence],
            )

    return RetrievalResult(
        task_type=plan.task_type,
        plan=plan_payload,
        evidence=evidence,
        documents=_documents_from_evidence(evidence),
        context=context,
        tool_events=tool_events,
        issues=gate.issues,
        rail_blocked=rail_blocked,
    )
