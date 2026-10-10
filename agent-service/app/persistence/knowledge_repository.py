from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

import psycopg
import psycopg.rows

from app.config import settings
from app.knowledge.models import (
    KnowledgeBase,
    KnowledgeChunk,
    PolicyDocument,
    TicketCase,
    TicketEvent,
)
from app.retrieval.types import RetrievalIndex

logger = logging.getLogger(__name__)

# The ANN index name is referenced by `has_ann_index()`, so it is a constant rather
# than a literal in two places that could drift.
ANN_INDEX_NAME = "idx_knowledge_chunk_embedding_hnsw"


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text input format: `[1.0,2.0,...]`.

    Built by hand rather than relying on `pgvector.psycopg.register_vector`, so the
    repository has no import-time dependency on the optional package and so the
    value is accepted by a plain `::vector` cast regardless of driver registration.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class PostgresKnowledgeRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.database_url

    def _connect(self):
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        return psycopg.connect(self.database_url, connect_timeout=1)

    def is_available(self) -> bool:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception:
            return False

    def ensure_schema(self) -> None:
        schema_path = Path(__file__).resolve().parents[2] / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    def count_documents(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM knowledge_document")
                return int(cur.fetchone()[0])

    def count_chunk_embeddings(self) -> int:
        """How many chunks actually carry a vector.

        This is the number that decides whether the ANN path is usable. It exists
        because the previous state of the world — `embedding` columns defined and
        always NULL — was indistinguishable from a working vector store unless
        somebody counted the rows.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(embedding) FROM knowledge_chunk")
                return int(cur.fetchone()[0])

    def count_chunks(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM knowledge_chunk")
                return int(cur.fetchone()[0])

    def has_ann_index(self) -> bool:
        """True when an HNSW/IVFFlat index exists on the chunk vector column.

        Without it `ORDER BY embedding <=> %s` is still *correct* but degrades to a
        sequential scan, so readiness has to report the difference rather than
        claiming a vector store is serving.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM pg_indexes
                    WHERE tablename = 'knowledge_chunk'
                      AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')
                    """
                )
                return int(cur.fetchone()[0]) > 0

    def ensure_ann_index(self) -> bool:
        """Create the HNSW index if it is missing. Returns whether one now exists.

        Built without a partial predicate: pgvector's HNSW index tolerates NULL
        vectors and the ANN scan skips them, whereas a partial index would only be
        usable by queries carrying a matching `WHERE embedding IS NOT NULL`, which
        the search does not need and would have to be kept in sync with.
        """
        ddl = (
            f"CREATE INDEX IF NOT EXISTS {ANN_INDEX_NAME} ON knowledge_chunk "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = {int(settings.vector_hnsw_m)}, "
            f"ef_construction = {int(settings.vector_hnsw_ef_construction)})"
        )
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(ddl)
            return True
        except Exception:
            logger.warning(
                "Could not create the HNSW index; dense search will fall back to a "
                "sequential scan",
                exc_info=True,
            )
            return False

    def search_chunks_by_vector(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 50,
        chunk_ids: set[str] | None = None,
        kb_ids: set[str] | None = None,
        roles: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """ANN search with every filter pushed into the statement.

        ACL is applied here rather than after retrieval. Filtering in Python would
        both leak (a forbidden chunk would be ranked before being dropped) and lose
        recall (a post-filtered top-50 can leave two rows). `roles=None` means "no
        scoping" — startup checks and evals; the request path always passes the
        caller's roles.

        `<=>` is cosine *distance*, so similarity is `1 - distance`, which keeps the
        score comparable with the in-memory backend's cosine similarity.
        """
        if not query_vector:
            return []

        conditions: list[str] = ["c.embedding IS NOT NULL"]
        params: dict[str, Any] = {
            "query_vector": _vector_literal(query_vector),
            "limit": max(1, int(top_k)),
            # pgvector's default ef_search is 40; higher trades latency for recall.
            "ef_search": max(1, int(settings.vector_hnsw_ef_search)),
        }
        if chunk_ids is not None:
            conditions.append("c.id = ANY(%(chunk_ids)s)")
            params["chunk_ids"] = list(chunk_ids)
        if kb_ids is not None:
            conditions.append("d.kb_id = ANY(%(kb_ids)s)")
            params["kb_ids"] = list(kb_ids)
        conditions.append("d.status = 'active'")
        if roles is not None:
            # Chunks inherit their document's ACL: a chunk whose document is not
            # visible to the caller must never be retrievable.
            conditions.append(
                "(d.allowed_roles = '[]'::jsonb OR d.allowed_roles ?| %(roles)s)"
            )
            params["roles"] = sorted(roles)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT c.*, 1 - (c.embedding <=> %(query_vector)s::vector) AS score
            FROM knowledge_chunk c
            JOIN knowledge_document d ON d.id = c.document_id
            WHERE {where}
            ORDER BY c.embedding <=> %(query_vector)s::vector
            LIMIT %(limit)s
        """
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                if settings.vector_store_enabled:
                    # Local to this transaction, which is exactly the scope we want.
                    cur.execute(
                        "SELECT set_config('hnsw.ef_search', %s, true)",
                        (str(params["ef_search"]),),
                    )
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [
            {"chunk": self._chunk_from_row(row), "score": float(row.get("score") or 0.0)}
            for row in rows
        ]

    @staticmethod
    def _chunk_from_row(row: dict[str, Any]) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=row["id"],
            document_id=row["document_id"],
            parent_chunk_id=row["parent_chunk_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            keywords=list(row["keywords"] or []),
            section_path=list(row["section_path"] or []),
            page=row["page"],
            token_count=row["token_count"],
        )

    def _persist_embeddings(
        self,
        chunks: list[KnowledgeChunk],
        only_missing: bool = False,
    ) -> int:
        """Embed chunks in batches, write the vectors, then build the ANN index.

        Runs in its own connection *after* the content transaction has committed, so
        an embedding provider outage degrades the dense channel (rows stay NULL, the
        lexical channels keep working) instead of losing the content write.
        """
        from app.retrieval.embedding import default_embedding_provider

        provider = default_embedding_provider()
        if provider is None or not chunks:
            return 0

        targets = chunks
        if only_missing:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM knowledge_chunk WHERE embedding IS NULL"
                    )
                    missing = {row[0] for row in cur.fetchall()}
            targets = [chunk for chunk in chunks if chunk.id in missing]
        if not targets:
            return 0

        from app.retrieval.cache import embed_with_cache

        written = 0
        for start in range(0, len(targets), settings.embedding_batch_size):
            window = targets[start : start + settings.embedding_batch_size]
            vectors = embed_with_cache([chunk.content for chunk in window], provider)
            payload = [
                {"id": chunk.id, "embedding": _vector_literal(vector)}
                for chunk, vector in zip(window, vectors)
                if vector
            ]
            if not payload:
                continue
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        UPDATE knowledge_chunk
                        SET embedding = %(embedding)s::vector
                        WHERE id = %(id)s
                        """,
                        payload,
                    )
            written += len(payload)

        if written:
            self.ensure_ann_index()
        return written

    def seed(self, index: RetrievalIndex) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Which chunks already have content, so the embedding pass can skip
                # re-embedding text that did not change. Read before the upserts.
                cur.execute("SELECT id, content FROM knowledge_chunk")
                existing_chunk_content = dict(cur.fetchall())

                for kb in index.knowledge_bases:
                    cur.execute(
                        """
                        INSERT INTO knowledge_base (
                            id, tenant_id, domain, name, description, keywords, acl, freshness_policy
                        ) VALUES (
                            %(id)s, 'default', %(domain)s, %(name)s, %(description)s,
                            %(keywords)s::jsonb, %(acl)s::jsonb, %(freshness)s::jsonb
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            domain = EXCLUDED.domain,
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            keywords = EXCLUDED.keywords,
                            acl = EXCLUDED.acl,
                            freshness_policy = EXCLUDED.freshness_policy
                        """,
                        {
                            "id": kb.id,
                            "domain": kb.domain,
                            "name": kb.name,
                            "description": kb.description,
                            "keywords": json.dumps(kb.keywords, ensure_ascii=False),
                            "acl": json.dumps(kb.allowed_roles, ensure_ascii=False),
                            "freshness": "{}",
                        },
                    )

                for document in index.policy_documents:
                    cur.execute(
                        """
                        INSERT INTO knowledge_document (
                            id, kb_id, title, content, source_type, version,
                            authority_level, effective_at, expires_at, status,
                            allowed_roles
                        ) VALUES (
                            %(id)s, %(kb_id)s, %(title)s, %(content)s, %(source_type)s,
                            %(version)s, %(authority_level)s, %(effective_at)s,
                            %(expires_at)s, %(status)s, %(allowed_roles)s::jsonb
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            version = EXCLUDED.version,
                            authority_level = EXCLUDED.authority_level,
                            effective_at = EXCLUDED.effective_at,
                            expires_at = EXCLUDED.expires_at,
                            status = EXCLUDED.status,
                            allowed_roles = EXCLUDED.allowed_roles
                        """,
                        {
                            "id": document.id,
                            "kb_id": document.kb_id,
                            "title": document.title,
                            "content": document.content,
                            "source_type": document.source_type,
                            "version": document.version,
                            "authority_level": document.authority_level,
                            "effective_at": document.effective_at,
                            "expires_at": document.expires_at,
                            "status": document.status,
                            "allowed_roles": json.dumps(
                                document.allowed_roles,
                                ensure_ascii=False,
                            ),
                        },
                    )

                for chunk in index.chunks:
                    # `embedding` is deliberately not touched here. A chunk whose
                    # content changed must lose its vector, because a stale vector
                    # silently ranks a chunk by text that no longer exists. The
                    # reset and the re-embed both happen in `_persist_embeddings`
                    # after this transaction commits, so a provider outage cannot
                    # roll back the content write.
                    content_changed = existing_chunk_content.get(chunk.id) not in (None, chunk.content)
                    cur.execute(
                        """
                        INSERT INTO knowledge_chunk (
                            id, document_id, parent_chunk_id, chunk_index, content,
                            keywords, section_path, page, token_count
                        ) VALUES (
                            %(id)s, %(document_id)s, %(parent_chunk_id)s, %(chunk_index)s,
                            %(content)s, %(keywords)s::jsonb, %(section_path)s::jsonb,
                            %(page)s, %(token_count)s
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            content = EXCLUDED.content,
                            keywords = EXCLUDED.keywords,
                            section_path = EXCLUDED.section_path,
                            token_count = EXCLUDED.token_count,
                            embedding = CASE
                                WHEN knowledge_chunk.content IS DISTINCT FROM EXCLUDED.content
                                THEN NULL
                                ELSE knowledge_chunk.embedding
                            END
                        """,
                        {
                            "id": chunk.id,
                            "document_id": chunk.document_id,
                            "parent_chunk_id": chunk.parent_chunk_id,
                            "chunk_index": chunk.chunk_index,
                            "content": chunk.content,
                            "keywords": json.dumps(chunk.keywords, ensure_ascii=False),
                            "section_path": json.dumps(chunk.section_path, ensure_ascii=False),
                            "page": chunk.page,
                            "token_count": chunk.token_count,
                        },
                    )
                    if content_changed:
                        logger.info(
                            "Chunk %s content changed; its vector is being recomputed", chunk.id
                        )

                for ticket in index.tickets:
                    cur.execute(
                        """
                        INSERT INTO ticket_case (
                            id, tenant_id, product, component, environment, status,
                            symptom, error_codes, root_cause, resolution, closed_at,
                            allowed_roles
                        ) VALUES (
                            %(id)s, %(tenant_id)s, %(product)s, %(component)s,
                            %(environment)s, %(status)s, %(symptom)s,
                            %(error_codes)s::jsonb, %(root_cause)s, %(resolution)s,
                            %(closed_at)s, %(allowed_roles)s::jsonb
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            product = EXCLUDED.product,
                            component = EXCLUDED.component,
                            environment = EXCLUDED.environment,
                            status = EXCLUDED.status,
                            symptom = EXCLUDED.symptom,
                            error_codes = EXCLUDED.error_codes,
                            root_cause = EXCLUDED.root_cause,
                            resolution = EXCLUDED.resolution,
                            closed_at = EXCLUDED.closed_at,
                            allowed_roles = EXCLUDED.allowed_roles
                        """,
                        {
                            "id": ticket.id,
                            "tenant_id": ticket.tenant_id,
                            "product": ticket.product,
                            "component": ticket.component,
                            "environment": ticket.environment,
                            "status": ticket.status,
                            "symptom": ticket.symptom,
                            "error_codes": json.dumps(ticket.error_codes, ensure_ascii=False),
                            "root_cause": ticket.root_cause,
                            "resolution": ticket.resolution,
                            "closed_at": ticket.closed_at,
                            "allowed_roles": json.dumps(
                                ticket.allowed_roles,
                                ensure_ascii=False,
                            ),
                        },
                    )

                for event in index.events:
                    cur.execute(
                        """
                        INSERT INTO ticket_event (
                            id, case_id, event_type, content, actor_hash, occurred_at
                        ) VALUES (
                            %(id)s, %(case_id)s, %(event_type)s, %(content)s,
                            %(actor_hash)s, %(occurred_at)s
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            event_type = EXCLUDED.event_type,
                            content = EXCLUDED.content,
                            occurred_at = EXCLUDED.occurred_at
                        """,
                        {
                            "id": event.id,
                            "case_id": event.case_id,
                            "event_type": event.event_type,
                            "content": event.content,
                            "actor_hash": event.actor_hash,
                            "occurred_at": event.occurred_at,
                        },
                    )

        # Embedding is a separate, best-effort phase. It runs after the content
        # transaction has committed, so a provider outage leaves the content written
        # and the vectors NULL — the dense channel degrades and says so, rather than
        # the ingest failing or the index going stale.
        try:
            written = self._persist_embeddings(list(index.chunks))
            if written:
                logger.info("Persisted %d chunk embedding(s)", written)
        except Exception:
            logger.warning("Chunk embedding pass failed", exc_info=True)

        # Writing knowledge invalidates any cached index, here as well as in the ingestion
        # entry point: a caller that reaches this repository directly would otherwise leave a
        # stale cache serving the pre-write content until its TTL expired. The import is local
        # because `app.knowledge.ingest` imports this module at module level, so a top-level
        # import back into it would be a cycle.
        from app.knowledge.ingest import invalidate_index

        invalidate_index()

    def load_index(self, roles: list[str] | None = None) -> RetrievalIndex:
        """Load the index, optionally scoped to a caller's roles.

        When `roles` is given the ACL filter runs **in SQL**, so a revoked
        permission takes effect as soon as the caller's next query runs instead of
        waiting for a cached in-memory copy to be trimmed. `None` means "no
        scoping" (startup checks, evals, admin views).
        """
        scoped = roles is not None
        role_list = list(roles or [])
        with self._connect() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                # Empty JSONB array means "visible to everyone"; otherwise the
                # caller must hold at least one of the listed roles.
                kb_where = (
                    "WHERE acl = '[]'::jsonb OR acl ?| %(roles)s" if scoped else ""
                )
                doc_where = (
                    "WHERE allowed_roles = '[]'::jsonb OR allowed_roles ?| %(roles)s"
                    if scoped
                    else ""
                )
                ticket_where = (
                    "WHERE allowed_roles = '[]'::jsonb OR allowed_roles ?| %(roles)s"
                    if scoped
                    else ""
                )
                params = {"roles": role_list}

                cur.execute(
                    f"SELECT * FROM knowledge_base {kb_where} ORDER BY id", params
                )
                kb_rows = cur.fetchall()
                cur.execute(
                    f"SELECT * FROM knowledge_document {doc_where} ORDER BY id", params
                )
                document_rows = cur.fetchall()

                document_ids = [row["id"] for row in document_rows]
                if scoped:
                    # Chunks inherit their document's ACL; a chunk whose document
                    # is not visible must never be retrievable.
                    cur.execute(
                        """
                        SELECT * FROM knowledge_chunk
                        WHERE document_id = ANY(%(document_ids)s)
                        ORDER BY document_id, chunk_index
                        """,
                        {"document_ids": document_ids},
                    )
                else:
                    cur.execute(
                        "SELECT * FROM knowledge_chunk ORDER BY document_id, chunk_index"
                    )
                chunk_rows = cur.fetchall()

                cur.execute(
                    f"SELECT * FROM ticket_case {ticket_where} ORDER BY id", params
                )
                ticket_rows = cur.fetchall()

                case_ids = [row["id"] for row in ticket_rows]
                if scoped:
                    cur.execute(
                        """
                        SELECT * FROM ticket_event
                        WHERE case_id = ANY(%(case_ids)s)
                        ORDER BY case_id, occurred_at
                        """,
                        {"case_ids": case_ids},
                    )
                else:
                    cur.execute("SELECT * FROM ticket_event ORDER BY case_id, occurred_at")
                event_rows = cur.fetchall()

        knowledge_bases = [
            KnowledgeBase(
                id=row["id"],
                name=row["name"],
                domain=row["domain"],
                description=row["description"],
                keywords=list(row["keywords"] or []),
                allowed_roles=list(row["acl"] or []),
            )
            for row in kb_rows
        ]
        policy_documents = [
            PolicyDocument(
                id=row["id"],
                kb_id=row["kb_id"],
                title=row["title"],
                content=row["content"],
                version=row["version"],
                authority_level=row["authority_level"],
                effective_at=row["effective_at"].isoformat() if row["effective_at"] else None,
                expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
                status=row["status"],
                source_type=row["source_type"],
                allowed_roles=list(row["allowed_roles"] or []),
                keywords=[],
            )
            for row in document_rows
        ]
        chunks = [
            KnowledgeChunk(
                id=row["id"],
                document_id=row["document_id"],
                parent_chunk_id=row["parent_chunk_id"],
                chunk_index=row["chunk_index"],
                content=row["content"],
                keywords=list(row["keywords"] or []),
                section_path=list(row["section_path"] or []),
                page=row["page"],
                token_count=row["token_count"],
            )
            for row in chunk_rows
        ]
        tickets = [
            TicketCase(
                id=row["id"],
                tenant_id=row["tenant_id"],
                product=row["product"],
                component=row["component"],
                environment=row["environment"],
                status=row["status"],
                symptom=row["symptom"],
                error_codes=list(row["error_codes"] or []),
                root_cause=row["root_cause"],
                resolution=row["resolution"],
                closed_at=row["closed_at"].isoformat() if row["closed_at"] else None,
                allowed_roles=list(row["allowed_roles"] or []),
                keywords=[],
            )
            for row in ticket_rows
        ]
        events = [
            TicketEvent(
                id=row["id"],
                case_id=row["case_id"],
                event_type=row["event_type"],
                content=row["content"],
                actor_hash=row["actor_hash"],
                occurred_at=row["occurred_at"].isoformat(),
            )
            for row in event_rows
        ]

        return RetrievalIndex(
            knowledge_bases=knowledge_bases,
            policy_documents=policy_documents,
            chunks=chunks,
            tickets=tickets,
            events=events,
        )

    def record_retrieval_run(
        self,
        request_id: str,
        task_type: str,
        plan: dict[str, Any],
        candidate_ids: list[str],
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retrieval_run (
                        id, request_id, task_type, plan, candidate_ids, latency_ms, token_usage
                    ) VALUES (
                        %(id)s, %(request_id)s, %(task_type)s, %(plan)s::jsonb,
                        %(candidate_ids)s::jsonb, 0, '{}'::jsonb
                    )
                    """,
                    {
                        "id": f"run_{request_id}",
                        "request_id": request_id,
                        "task_type": task_type,
                        "plan": json.dumps(plan, ensure_ascii=False),
                        "candidate_ids": json.dumps(candidate_ids, ensure_ascii=False),
                    },
                )
