"""Knowledge index construction, cache management, and the write entry point.

The index used to be memoised with `lru_cache(maxsize=1)`, i.e. built once and
kept forever. That is a **security defect**, not just a staleness annoyance:
permission metadata is part of the index, so revoking a role's access to a
document had no effect until the process restarted. It also meant any knowledge
update required a deploy.

The cache is now:
* bounded by a TTL (`settings.index_cache_ttl_seconds`),
* explicitly invalidatable (`invalidate_index()`), so a permission or content
  change can take effect immediately, and
* role-aware when PostgreSQL is the source of truth, so the ACL filter is applied
  by the query instead of by trimming an in-memory copy.

Invalidation is wired into the write path, not left to the caller: every write of
knowledge content or ACLs goes through `ingest_knowledge_index()`, which drops the
cached copy *after* the data changed. An operator who changes rows out of band
(manual SQL, a migration) can force the same thing through
`POST /v1/knowledge/index/refresh`.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import settings
from app.knowledge.chunking import chunk_policy_document
from app.knowledge.corpus import (
    KNOWLEDGE_BASES,
    POLICY_DOCUMENTS,
    TICKET_CASES,
    TICKET_EVENTS,
)
from app.persistence.knowledge_repository import PostgresKnowledgeRepository
from app.retrieval.types import RetrievalIndex

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cached_index: RetrievalIndex | None = None
_cached_at: float = 0.0
_cached_roles: frozenset[str] | None = None
_cached_source: str | None = None
# Bumped by every invalidation. A build that started before a write must not
# publish its (now stale) index over the cache the write just cleared.
_generation: int = 0


def _build_in_memory_index() -> RetrievalIndex:
    chunks = []
    for document in POLICY_DOCUMENTS:
        chunks.extend(chunk_policy_document(document))

    return RetrievalIndex(
        knowledge_bases=KNOWLEDGE_BASES,
        policy_documents=POLICY_DOCUMENTS,
        chunks=chunks,
        tickets=TICKET_CASES,
        events=TICKET_EVENTS,
    )


def invalidate_index() -> None:
    """Drop the cached index so the next query rebuilds it.

    Called by `ingest_knowledge_index()` as part of every knowledge write, and by
    the operator refresh endpoint. Cross-replica invalidation needs a pub/sub or
    a short TTL — both are supported: the TTL bounds staleness even without a
    signal.
    """
    global _cached_index, _cached_at, _cached_roles, _cached_source, _generation
    with _lock:
        _cached_index = None
        _cached_at = 0.0
        _cached_roles = None
        _cached_source = None
        _generation += 1
    logger.info("Knowledge index cache invalidated")


def index_cache_state() -> dict[str, object]:
    """Observability hook for the readiness/health surface.

    An operator has to be able to judge staleness from this alone: how old the
    serving copy is, how long it stays fresh, and what it was built from
    (source, documents, roles). The original keys are kept as-is because the
    readiness contract is asserted on them.
    """
    index = _cached_index
    cached = index is not None
    ttl = settings.index_cache_ttl_seconds
    age = round(time.monotonic() - _cached_at, 3) if cached else None
    return {
        "cached": cached,
        "age_seconds": age,
        "ttl_seconds": ttl,
        "expires_in_seconds": (
            round(max(0.0, ttl - age), 3) if age is not None else None
        ),
        "role_scoped": _cached_roles is not None,
        "roles": sorted(_cached_roles) if _cached_roles else [],
        "source": _cached_source if cached else None,
        "generation": _generation,
        "documents": len(index.policy_documents) if index else 0,
        "chunks": len(index.chunks) if index else 0,
        "knowledge_bases": len(index.knowledge_bases) if index else 0,
        "tickets": len(index.tickets) if index else 0,
    }


def _write_index(
    repository: PostgresKnowledgeRepository,
    index: RetrievalIndex,
) -> None:
    """Seed the store, then drop the cache built from the previous content.

    Order matters: invalidating before the write would let a concurrent query
    rebuild from the old rows and keep them cached for a whole TTL. Availability
    checks and schema setup belong to the caller, so the seeding path inside
    `_load_from_postgres` does not pay for them twice.
    """
    repository.seed(index)
    invalidate_index()


def ingest_knowledge_index(
    index: RetrievalIndex,
    repository: PostgresKnowledgeRepository | None = None,
) -> bool:
    """Write knowledge content and ACLs, invalidating the index as part of it.

    This is the service's ingestion entry point: anything that changes documents,
    chunks, tickets or their `allowed_roles` must come through here so a stale
    index cannot outlive the data it was built from. Returns `False` when there is
    no reachable system of record and nothing was written.
    """
    repo = repository or PostgresKnowledgeRepository()
    if not repo.is_available():
        logger.warning(
            "Knowledge ingestion skipped: no reachable system of record; "
            "the in-process corpus stays in use"
        )
        return False
    repo.ensure_schema()
    _write_index(repo, index)
    logger.info(
        "Knowledge index ingested: %d document(s), %d chunk(s)",
        len(index.policy_documents),
        len(index.chunks),
    )
    return True


def _load_from_postgres(roles: frozenset[str] | None) -> RetrievalIndex | None:
    repository = PostgresKnowledgeRepository()
    if not repository.is_available():
        return None

    repository.ensure_schema()
    if repository.count_documents() == 0:
        # Seeding is a write; it invalidates the cache through the same path as
        # any other ingestion.
        _write_index(repository, _build_in_memory_index())
    # Push the ACL filter into the query when we know the caller's roles; the
    # in-Python `_can_access_document` check stays as defence in depth.
    if roles is None:
        return repository.load_index()
    return repository.load_index(roles=sorted(roles))


def build_seed_index(roles: frozenset[str] | None = None) -> RetrievalIndex:
    """Return the retrieval index, rebuilding it when the cache is stale.

    `roles` scopes the index to what the caller may see when PostgreSQL is the
    source of truth. Callers that do not need scoping (startup checks, evals)
    pass nothing and get the full index.
    """
    global _cached_index, _cached_at, _cached_roles, _cached_source

    ttl = max(0.0, settings.index_cache_ttl_seconds)
    now = time.monotonic()

    with _lock:
        fresh = (
            _cached_index is not None
            and (now - _cached_at) < ttl
            and _cached_roles == roles
        )
        if fresh:
            return _cached_index
        generation = _generation

    # Build outside the lock: the underlying load is idempotent and a concurrent
    # rebuild is cheaper than serialising every query behind a slow database read.
    index: RetrievalIndex | None = None
    source = "memory"
    if settings.retrieval_use_postgres:
        try:
            index = _load_from_postgres(roles)
            if index is not None:
                source = "postgres"
        except Exception:
            logger.exception("Failed to load the knowledge index from PostgreSQL")

    if index is None:
        if roles is not None:
            # The in-memory corpus carries static ACLs, so a full index is correct
            # and the per-query `_can_access_*` checks still apply.
            return build_seed_index(None)
        index = _build_in_memory_index()
        source = "memory"

    with _lock:
        if _generation != generation:
            # A knowledge write invalidated the cache while this index was being
            # built, so it is already stale: serve it to this caller but leave the
            # cache empty for the next query to rebuild from the new data.
            logger.info("Discarding a knowledge index built before a concurrent write")
            return index
        _cached_index = index
        _cached_at = time.monotonic()
        _cached_roles = roles
        _cached_source = source
    return index
