"""Knowledge index construction and cache management.

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

    Call this after any knowledge or permission change (and from the admin API
    that performs one). Cross-replica invalidation needs a pub/sub or a short
    TTL — both are supported: the TTL bounds staleness even without a signal.
    """
    global _cached_index, _cached_at, _cached_roles
    with _lock:
        _cached_index = None
        _cached_at = 0.0
        _cached_roles = None
    logger.info("Knowledge index cache invalidated")


def index_cache_state() -> dict[str, object]:
    """Observability hook for the readiness/health surface."""
    return {
        "cached": _cached_index is not None,
        "age_seconds": round(time.monotonic() - _cached_at, 3) if _cached_index else None,
        "ttl_seconds": settings.index_cache_ttl_seconds,
        "role_scoped": _cached_roles is not None,
    }


def _load_from_postgres(roles: frozenset[str] | None) -> RetrievalIndex | None:
    repository = PostgresKnowledgeRepository()
    if not repository.is_available():
        return None

    repository.ensure_schema()
    if repository.count_documents() == 0:
        repository.seed(_build_in_memory_index())
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
    global _cached_index, _cached_at, _cached_roles

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

    # Build outside the lock: the underlying load is idempotent and a concurrent
    # rebuild is cheaper than serialising every query behind a slow database read.
    index: RetrievalIndex | None = None
    if settings.retrieval_use_postgres:
        try:
            index = _load_from_postgres(roles)
        except Exception:
            logger.exception("Failed to load the knowledge index from PostgreSQL")

    if index is None:
        if roles is not None:
            # The in-memory corpus carries static ACLs, so a full index is correct
            # and the per-query `_can_access_*` checks still apply.
            return build_seed_index(None)
        index = _build_in_memory_index()

    with _lock:
        _cached_index = index
        _cached_at = now
        _cached_roles = roles
    return index
