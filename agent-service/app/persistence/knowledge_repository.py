from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    def seed(self, index: RetrievalIndex) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
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
                            token_count = EXCLUDED.token_count
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
