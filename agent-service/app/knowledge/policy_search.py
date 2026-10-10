from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.config import settings
from app.knowledge.models import Evidence, KnowledgeChunk, PolicyDocument
from app.knowledge.text import token_count
from app.retrieval.hybrid_search import hybrid_retrieve_chunks
from app.retrieval.types import DenseStoreLike, RetrievalIndex


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


def _index_chunks_by_id(index: RetrievalIndex) -> dict[str, KnowledgeChunk]:
    return {chunk.id: chunk for chunk in index.chunks}


def _siblings(
    index: RetrievalIndex,
    chunk: KnowledgeChunk,
    by_id: dict[str, KnowledgeChunk],
) -> list[KnowledgeChunk]:
    """The chunk's siblings under the same parent, in reading order.

    Sibling expansion is what makes a small chunk retrievable without losing the
    sentence that qualifies it. A sentence-split policy routinely puts "……除外" in
    the next chunk, and returning the match alone drops exactly the clause that
    changes the answer.
    """
    parent_id = chunk.parent_chunk_id
    siblings = [
        candidate
        for candidate in index.chunks
        if candidate.document_id == chunk.document_id
        and (
            candidate.parent_chunk_id == parent_id
            if parent_id is not None
            else candidate.parent_chunk_id is None
        )
    ]
    siblings.sort(key=lambda candidate: candidate.chunk_index)
    return siblings or [by_id.get(chunk.id, chunk)]


def _assemble_block(
    index: RetrievalIndex,
    chunk: KnowledgeChunk,
    by_id: dict[str, KnowledgeChunk],
) -> tuple[str, list[str], bool]:
    """Build the context block for a matched chunk.

    Returns `(content, section_path, truncated)`.

    This used to return the parent chunk's content unconditionally, and since a
    policy's parent is its ROOT chunk, every hit was replaced by the *entire
    document*. That discarded the precision the chunk-level ranking had just paid
    for and pushed whole policies into the prompt — the "stuff every document into
    the context" anti-pattern, arrived at by accident.
    """
    section_path = list(chunk.section_path)
    content = chunk.content
    truncated = False

    for sibling in _siblings(index, chunk, by_id):
        if sibling.id == chunk.id:
            continue
        if token_count(content) >= settings.retrieval_evidence_max_tokens:
            truncated = True
            break
        content = f"{content}\n{sibling.content}"

    if token_count(content) > settings.retrieval_evidence_max_tokens:
        content = _truncate_to_tokens(content, settings.retrieval_evidence_max_tokens)
        truncated = True

    return content, section_path, truncated


def _truncate_to_tokens(text: str, budget: int) -> str:
    """Character-truncate toward a token budget.

    Approximate on purpose: this is a prompt-size guard, not a tokenizer. It errs
    small, and `metadata.truncated` records that it happened so a short evidence
    block is never mistaken for a short source document.
    """
    if budget <= 0:
        return text
    # CJK is roughly one token per character; ASCII roughly four characters per
    # token. Using the character count as an upper bound is safe for both.
    return text[:budget].rstrip() + "……"


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
    dense_store: DenseStoreLike | None = None,
    filters: dict[str, Any] | None = None,
) -> tuple[list[Evidence], list[str], list[dict[str, Any]]]:
    allowed_documents = [
        document
        for document in index.policy_documents
        if document.kb_id in kb_ids
        and document.status == "active"
        and _can_access_document(document, user_context)
        and _passes_filters(document, filters)
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
        dense_store=dense_store,
    )

    by_id = _index_chunks_by_id(index)
    evidence: list[Evidence] = []
    per_document: dict[str, int] = defaultdict(int)
    seen_chunks: set[str] = set()
    #: `document_id -> (document, best chunk)` so a document whose only hit fell
    #: outside the caps can still be cited instead of silently dropped.
    over_cap: dict[str, tuple[PolicyDocument, Any]] = {}
    dropped_mentions: set[str] = set()

    def build(candidate) -> Evidence | None:
        chunk = candidate.item
        document = document_map.get(chunk.document_id)
        if not document:
            return None
        content, section_path, truncated = _assemble_block(index, chunk, by_id)
        return Evidence(
            id=f"ev_policy_{document.id}_{chunk.id}",
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
                "chunk_id": chunk.id,
                "truncated": truncated,
                "citation": f"{document.id} v{document.version}",
            },
            score=round(candidate.score, 4),
            matched_by=candidate.matched_by,
        )

    def add_candidate(candidate) -> None:
        chunk = candidate.item
        if chunk.id in seen_chunks:
            return
        document = document_map.get(chunk.document_id)
        if not document:
            return
        if per_document[document.id] >= settings.retrieval_evidence_per_document:
            over_cap.setdefault(document.id, (document, candidate))
            return
        item = build(candidate)
        if item is None:
            return
        seen_chunks.add(chunk.id)
        per_document[document.id] += 1
        evidence.append(item)

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
            dense_store=dense_store,
        )
        for candidate in per_kb_chunks:
            add_candidate(candidate)

    # A document that was ranked but exceeded its per-document cap must still be
    # named. Dropping it would make the evidence gate read "no policy found" for a
    # query that did retrieve one, which is a worse error than a verbose answer.
    for document_id, (document, candidate) in over_cap.items():
        if any(item.source_id == document_id for item in evidence):
            continue
        item = build(candidate)
        if item is None:
            continue
        # Cited so the document is not invisible, but flagged: the reader should know
        # this one exceeded its per-document share rather than treat it as complete.
        item.metadata["truncated"] = True
        evidence.append(item)
        dropped_mentions.add(document_id)

    # Deliberately NOT re-sorted by score. The order here is the output of the whole
    # ranking pipeline — RRF fusion, then the heuristic and model rerankers — and
    # sorting by raw score at the end would silently discard the reranker's judgement
    # (a promoted chunk has a modest fused score but is the correct answer). The
    # fallback and over-cap passes above already append, so first rank is preserved.
    issues = _version_conflicts(index, set(kb_ids), user_context)
    if dropped_mentions:
        issues.append(
            f"{len(dropped_mentions)} 篇制度因内容较长被截断，仅保留最相关片段。"
        )
    tool_events = [
        {
            "type": "tool_result",
            "toolCallId": "policy_hybrid_search",
            "name": "policy_hybrid_search",
            "success": bool(evidence),
            "message": f"制度混合检索完成，命中 {len(evidence)} 条 active 制度片段。",
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


def _passes_filters(document: PolicyDocument, filters: dict[str, Any] | None) -> bool:
    """Apply the plan's metadata filters to a document.

    `time_range` used to be extracted by query understanding and then dropped on the
    floor: asking for a year's policy returned the current one, because nothing
    turned `{"year": 2024}` into a predicate. Only documents in force during the
    requested window are now eligible.

    A document with no `effective_at`/`expires_at` is treated as always in force,
    which is the conservative reading for a corpus whose dates are optional.
    """
    if not filters:
        return True
    time_range = filters.get("time_range")
    if not isinstance(time_range, dict):
        return True
    year = time_range.get("year")
    if not isinstance(year, int):
        return True
    start = f"{year:04d}-01-01"
    end = f"{year:04d}-12-31"
    if document.effective_at and document.effective_at > end:
        return False
    if document.expires_at and document.expires_at < start:
        return False
    return True

