from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.knowledge.models import Evidence, PolicyDocument
from app.retrieval.hybrid_search import hybrid_retrieve_chunks
from app.retrieval.types import RetrievalIndex


def _can_access_document(
    document: PolicyDocument,
    user_context: dict[str, Any] | None,
) -> bool:
    if not document.allowed_roles:
        return True
    if not user_context:
        return False
    roles = set(user_context.get("roles", []))
    return bool(roles.intersection(document.allowed_roles))


def _parent_content(
    index: RetrievalIndex,
    chunk_id: str,
) -> tuple[str, list[str]]:
    chunk = next((item for item in index.chunks if item.id == chunk_id), None)
    if not chunk:
        return "", []

    if chunk.parent_chunk_id:
        parent = next(
            (item for item in index.chunks if item.id == chunk.parent_chunk_id),
            None,
        )
        if parent:
            return parent.content, parent.section_path
    return chunk.content, chunk.section_path


def _version_conflicts(
    index: RetrievalIndex,
    kb_ids: set[str],
    user_context: dict[str, Any] | None,
) -> list[str]:
    grouped: dict[str, list[PolicyDocument]] = defaultdict(list)
    for document in index.policy_documents:
        if document.kb_id in kb_ids and _can_access_document(document, user_context):
            grouped[document.base_key].append(document)

    conflicts = []
    for _title, documents in grouped.items():
        statuses = {document.status for document in documents}
        if "active" in statuses and "archived" in statuses:
            active_versions = sorted(
                (document.version for document in documents if document.status == "active"),
                reverse=True,
            )
            conflicts.append(
                f"{documents[0].title} 同时存在当前版本 {active_versions[0]} 与历史版本，"
                "历史版本仅作背景参考，不作为当前执行依据。"
            )
    return conflicts


def retrieve_policy_evidence(
    index: RetrievalIndex,
    query: str,
    kb_ids: list[str],
    user_context: dict[str, Any] | None = None,
    top_k: int = 8,
    rerank_client=None,
    embedding_provider=None,
) -> tuple[list[Evidence], list[str], list[dict[str, Any]]]:
    allowed_documents = [
        document
        for document in index.policy_documents
        if document.kb_id in kb_ids
        and document.status == "active"
        and _can_access_document(document, user_context)
    ]
    document_map = {document.id: document for document in allowed_documents}
    ranked_chunks = hybrid_retrieve_chunks(
        query,
        index,
        set(kb_ids),
        include_archived=False,
        top_k=top_k,
        rerank_client=rerank_client,
        embedding_provider=embedding_provider,
    )

    evidence: list[Evidence] = []
    seen_documents: set[str] = set()

    def add_candidate(candidate) -> None:
        chunk = candidate.item
        document = document_map.get(chunk.document_id)
        if not document or document.id in seen_documents:
            return
        seen_documents.add(document.id)

        content, section_path = _parent_content(index, chunk.id)
        evidence.append(
            Evidence(
                id=f"ev_policy_{document.id}",
                source_type="policy",
                source_id=document.id,
                kb_id=document.kb_id,
                title=document.title,
                content=content or document.content,
                metadata={
                    "version": document.version,
                    "authority_level": document.authority_level,
                    "effective_at": document.effective_at,
                    "expires_at": document.expires_at,
                    "status": document.status,
                    "section_path": section_path,
                    "citation": f"{document.id} v{document.version}",
                },
                score=round(candidate.score, 4),
                matched_by=candidate.matched_by,
            )
        )

    for candidate in ranked_chunks:
        add_candidate(candidate)

    covered_kbs = {item.kb_id for item in evidence}
    for kb_id in set(kb_ids) - covered_kbs:
        per_kb_chunks = hybrid_retrieve_chunks(
            query,
            index,
            {kb_id},
            include_archived=False,
            top_k=3,
            rerank_client=rerank_client,
            embedding_provider=embedding_provider,
        )
        for candidate in per_kb_chunks:
            add_candidate(candidate)

    issues = _version_conflicts(index, set(kb_ids), user_context)
    tool_events = [
        {
            "type": "tool_result",
            "toolCallId": "policy_hybrid_search",
            "name": "policy_hybrid_search",
            "success": bool(evidence),
            "message": f"制度混合检索完成，命中 {len(evidence)} 条 active 制度。",
            "resultCount": len(evidence),
            "sources": [
                {
                    "title": item.metadata.get("citation", item.title),
                    "url": "",
                    "snippet": item.content[:160],
                }
                for item in evidence
            ],
        }
    ]
    return evidence, issues, tool_events
