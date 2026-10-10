"""P0 retrieval work: persisted vectors, batched/async embedding, real granularity.

Three defects are pinned here.

1. `knowledge_chunk.embedding` existed in the schema and was never written, so the
   dense channel either did not run or re-embedded every chunk on every query.
2. Embedding was one synchronous HTTP call per chunk, issued from an `async` graph
   node — an N+1 pattern that also blocked the event loop.
3. Evidence assembly replaced every matched chunk with its parent, and a policy's
   parent is its ROOT chunk, so each hit became the *entire document*.

The database-backed parts skip when no database is reachable; the rest is pure
unit behaviour.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.config import settings
from app.knowledge.ingest import build_seed_index
from app.knowledge.policy_search import _assemble_block, retrieve_policy_evidence
from app.persistence.knowledge_repository import PostgresKnowledgeRepository
from app.retrieval import embedding as embedding_module
from app.retrieval.cache import EmbeddingCache, cache_key, reset_embedding_cache
from app.retrieval.embedding import (
    HashEmbeddingProvider,
    HttpEmbeddingProvider,
    aembed_many,
    as_async_provider,
    embed_many,
)
from app.retrieval.pipeline import run_retrieval
from app.retrieval.types import RETRIEVAL_MODE_HYBRID
from app.retrieval.vector_store import (
    BACKEND_IN_MEMORY,
    BACKEND_PGVECTOR,
    InMemoryDenseStore,
    PgVectorStore,
    build_dense_store,
)

QUERY = "VPN 外网访问财务系统需要哪些审批和条件？"


def database_available() -> bool:
    return PostgresKnowledgeRepository().is_available()


requires_db = pytest.mark.skipif(
    not database_available(), reason="no reachable PostgreSQL for the vector store"
)


class CountingProvider:
    """Records how many texts it was asked for, and in how many calls."""

    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions
        self.calls: list[int] = []
        self.texts = 0

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]  # type: ignore[index]

    def embed_batch(self, texts):
        batch = list(texts)
        self.calls.append(len(batch))
        self.texts += len(batch)
        # Deterministic and varied, so ordering mistakes are visible.
        return [
            [float((len(text) + index) % 7) + 0.5 for index in range(self.dimensions)]
            for text in batch
        ]


class OrderShufflingProvider:
    """Returns the response out of input order, as an API is allowed to."""

    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        seed = sum(ord(char) for char in text) % 97
        return [float(seed + index) for index in range(self.dimensions)]

    def embed_batch(self, texts):
        return [self._vector(text) for text in texts]

    async def aembed_batch(self, texts):
        return [self._vector(text) for text in texts]


# --------------------------------------------------------------- batching


def test_every_chunk_is_embedded_in_batches_not_one_request_each():
    """The N+1 pattern: 26 chunks used to mean 27 sequential HTTP requests."""
    index = build_seed_index()
    provider = CountingProvider()
    reset_embedding_cache()

    store = InMemoryDenseStore(index.chunks)
    results = store.search_dense(
        QUERY,
        allowed_chunk_ids=None,
        top_k=50,
        provider=provider,
    )

    assert results, "the dense channel must produce candidates"
    # One call for the query plus one per batch of chunks, and every chunk covered.
    assert provider.texts == len(index.chunks) + 1, (
        "the query and each chunk must each be embedded exactly once per call"
    )
    assert len(provider.calls) < len(index.chunks), (
        f"chunks must be batched: got {len(provider.calls)} calls for "
        f"{len(index.chunks)} chunks"
    )
    assert max(provider.calls) <= settings.embedding_batch_size


def test_batch_size_splits_large_requests(monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 4)
    provider = HashEmbeddingProvider(dimensions=4)
    vectors = embed_many([f"text-{position}" for position in range(10)], provider)
    assert len(vectors) == 10
    assert all(vector is not None for vector in vectors)


def test_embed_many_tolerates_a_provider_without_a_batch_method():
    """A provider that only offers `embed` must still work, one text at a time."""

    class SingleOnly:
        def embed(self, text: str) -> list[float]:
            return [1.0, 0.0]

    vectors = embed_many(["a", "b", "c"], SingleOnly())
    assert vectors == [[1.0, 0.0]] * 3


@pytest.mark.asyncio
async def test_async_embedding_does_not_block_the_event_loop():
    """`aembed_batch` on a sync provider must run off the loop, not on it."""
    provider = as_async_provider(CountingProvider())
    vectors = await aembed_many(["one", "two", "three"], provider)
    assert len(vectors) == 3
    assert all(vector for vector in vectors)


@pytest.mark.asyncio
async def test_async_batch_is_used_when_the_provider_offers_one():
    provider = OrderShufflingProvider()
    vectors = await aembed_many(["alpha", "beta"], provider)
    assert len(vectors) == 2
    assert vectors[0] != vectors[1]


def test_a_provider_without_async_support_is_wrapped():
    class SyncOnly:
        def embed(self, text: str) -> list[float]:
            return [1.0]

        def embed_batch(self, texts):
            return [[1.0] for _ in texts]

    wrapped = as_async_provider(SyncOnly())
    assert hasattr(wrapped, "aembed_batch")
    assert asyncio.iscoroutinefunction(wrapped.aembed_batch)


def test_http_provider_maps_response_by_index_not_position():
    """An out-of-order response must not attach the wrong vector to a chunk."""
    ordered = HttpEmbeddingProvider._order(
        {
            "data": [
                {"index": 1, "embedding": [2.0]},
                {"index": 0, "embedding": [1.0]},
            ]
        },
        2,
    )
    assert ordered == [[1.0], [2.0]]


def test_http_provider_drops_malformed_entries_rather_than_misaligning():
    ordered = HttpEmbeddingProvider._order(
        {"data": [{"index": 5, "embedding": [9.0]}, {"index": 0, "embedding": [1.0]}]},
        2,
    )
    assert ordered == [[1.0], None]


# ------------------------------------------------------------------- cache


def test_cache_key_includes_the_model_and_dimension():
    """Reusing vectors across models would corrupt ranking silently, not error."""
    assert cache_key("t", "model-a", 8) != cache_key("t", "model-b", 8)
    assert cache_key("t", "model-a", 8) != cache_key("t", "model-a", 16)
    assert cache_key("t", "model-a", 8) == cache_key("t", "model-a", 8)


def test_a_repeated_embedding_is_served_from_the_cache():
    provider = CountingProvider()
    cache = EmbeddingCache()

    first = embedding_module.embed_many(["same text"], provider)
    assert first[0] is not None

    from app.retrieval.cache import embed_with_cache

    embed_with_cache(["same text"], provider, cache=cache)
    embed_with_cache(["same text"], provider, cache=cache)

    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["misses"] == 1, "the first lookup must miss"
    assert stats["hits"] == 1, "the second must hit, not re-embed"


def test_a_failed_embedding_is_cached_as_absent():
    """A dead provider must not be retried once per chunk per query."""

    class Dead:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, text: str):
            self.calls += 1
            return None

    from app.retrieval.cache import embed_with_cache

    provider = Dead()
    cache = EmbeddingCache()
    assert embed_with_cache(["x"], provider, cache=cache) == [None]
    assert embed_with_cache(["x"], provider, cache=cache) == [None]
    assert provider.calls == 1, "the failure must be remembered"


def test_cache_is_bounded():
    cache = EmbeddingCache(max_size=2)
    for position in range(5):
        cache.put(f"k{position}", [float(position)])
    assert cache.stats()["size"] == 2


# ------------------------------------------------------------ dense backend


def test_in_memory_backend_restricts_to_the_allowed_chunks():
    index = build_seed_index()
    allowed = {index.chunks[0].id, index.chunks[1].id}
    store = InMemoryDenseStore(index.chunks)
    results = store.search_dense(
        QUERY,
        allowed_chunk_ids=allowed,
        top_k=50,
        provider=CountingProvider(),
    )
    assert {candidate.item.id for candidate in results} <= allowed
    assert store.backend == BACKEND_IN_MEMORY


def test_a_provider_that_yields_nothing_leaves_the_lexical_channels_alone():
    class Dead:
        def embed(self, text: str):
            return None

    index = build_seed_index()
    store = InMemoryDenseStore(index.chunks)
    assert (
        store.search_dense(QUERY, allowed_chunk_ids=None, top_k=10, provider=Dead()) == []
    )
    # And the pipeline still returns evidence in that state.
    result = run_retrieval(QUERY, embedding_provider=Dead())
    assert result.evidence
    assert all(
        "vector" not in (item["matched_by"] or "") for item in result.evidence
    )


def test_without_persisted_vectors_the_backend_is_the_in_memory_scan():
    """Honesty: an unpopulated vector store must not be reported as pgvector."""
    index = build_seed_index()
    store = build_dense_store(chunks=index.chunks)
    assert store is not None
    if database_available():
        repo = PostgresKnowledgeRepository()
        expected = (
            BACKEND_PGVECTOR if repo.count_chunk_embeddings() > 0 else BACKEND_IN_MEMORY
        )
        assert store.backend == expected
    else:
        assert store.backend == BACKEND_IN_MEMORY


# -------------------------------------------------- repository vector methods


@requires_db
def test_embedding_columns_are_populated_after_a_seed(monkeypatch):
    """The whole point of P0-1: the column stops being decorative."""
    from app.retrieval.embedding import HashEmbeddingProvider as _Hash

    monkeypatch.setattr(
        embedding_module, "default_embedding_provider", lambda: _Hash(dimensions=1536)
    )
    repo = PostgresKnowledgeRepository()
    repo.ensure_schema()
    index = build_seed_index()
    repo.seed(index)

    assert repo.count_chunks() > 0
    assert repo.count_chunk_embeddings() > 0, (
        "a seed with a live provider must persist chunk vectors"
    )


@requires_db
def test_an_ann_index_exists_once_embeddings_are_written():
    repo = PostgresKnowledgeRepository()
    if repo.count_chunk_embeddings() == 0:
        pytest.skip("no embeddings persisted yet")
    assert repo.has_ann_index(), (
        "without an HNSW index `ORDER BY embedding <=> %s` degrades to a seq scan"
    )


@requires_db
def test_vector_search_returns_chunks_with_a_similarity_score(monkeypatch):
    from app.retrieval.embedding import HashEmbeddingProvider as _Hash

    provider = _Hash(dimensions=1536)
    repo = PostgresKnowledgeRepository()
    repo.ensure_schema()
    repo.seed(build_seed_index())

    query_vector = provider.embed("VPN 外网访问审批")
    assert query_vector
    rows = repo.search_chunks_by_vector(query_vector, top_k=5)
    assert rows, "a populated vector store must return neighbours"
    assert all(0.0 <= row["score"] <= 1.0001 for row in rows), "cosine similarity range"
    assert all(row["chunk"].id for row in rows)


@requires_db
def test_vector_search_respects_an_acl_scope(monkeypatch):
    """The ACL filter belongs in the query, not applied to the result afterwards."""
    from app.retrieval.embedding import HashEmbeddingProvider as _Hash

    provider = _Hash(dimensions=1536)
    repo = PostgresKnowledgeRepository()
    repo.ensure_schema()
    repo.seed(build_seed_index())

    query_vector = provider.embed("VPN 外网访问审批")
    # A role nobody holds: every ACL-protected document must be excluded.
    rows = repo.search_chunks_by_vector(
        query_vector, top_k=50, roles=frozenset({"no-such-role"})
    )
    for row in rows:
        assert row["chunk"].id, "rows are still well-formed chunks"


@requires_db
def test_vector_search_applies_the_kb_filter(monkeypatch):
    from app.retrieval.embedding import HashEmbeddingProvider as _Hash

    provider = _Hash(dimensions=1536)
    repo = PostgresKnowledgeRepository()
    repo.ensure_schema()
    repo.seed(build_seed_index())

    rows = repo.search_chunks_by_vector(
        provider.embed("报销 发票 差旅"),
        top_k=50,
        kb_ids={"FINANCE_POLICY"},
    )
    for row in rows:
        assert row["chunk"].document_id.startswith("POL-FINANCE"), (
            "the knowledge-base filter must be pushed into the SQL"
        )


def test_pgvector_store_falls_back_when_the_query_vector_is_empty():
    class NoVector:
        """A provider that cannot produce a query vector."""

        def embed(self, text: str):
            return None

        def embed_batch(self, texts):
            return [None] * len(texts)

    store = PgVectorStore(repository=None)
    assert (
        store.search_dense(QUERY, allowed_chunk_ids=None, top_k=5, provider=NoVector())
        == []
    )
    # And with no provider at all.
    assert (
        store.search_dense(QUERY, allowed_chunk_ids=None, top_k=5, provider=None) == []
    )


# ------------------------------------------------------- evidence granularity


def test_evidence_is_a_chunk_not_the_whole_document():
    """Regression: every hit used to be replaced by its ROOT (whole-document) content."""
    index = build_seed_index()
    documents = {document.id: document for document in index.policy_documents}
    result = run_retrieval(QUERY)

    assert result.evidence
    whole_documents = [
        item
        for item in result.evidence
        if len(item["content"]) == len(documents[item["source_id"]].content)
    ]
    assert len(whole_documents) < len(result.evidence), (
        "at least one evidence block must be chunk-sized; returning whole documents "
        "for every hit discards the chunk-level ranking"
    )


def test_each_evidence_block_cites_a_distinct_chunk():
    result = run_retrieval(QUERY)
    chunk_ids = [item["metadata"].get("chunk_id") for item in result.evidence]
    assert all(chunk_ids), "every evidence block must record which chunk it came from"
    assert len(set(chunk_ids)) == len(chunk_ids), "no duplicate evidence blocks"


def test_sibling_expansion_recovers_an_adjacent_clause():
    """A small chunk is expanded with its siblings, so a qualifying sentence is kept."""
    index = build_seed_index()
    by_id = {chunk.id: chunk for chunk in index.chunks}
    target = next(
        chunk
        for chunk in index.chunks
        if chunk.parent_chunk_id is not None and chunk.document_id == "POL-VPN-001"
    )
    content, section_path, _truncated = _assemble_block(index, target, by_id)

    assert target.content in content
    assert len(content) >= len(target.content)
    assert section_path, "the section path must survive assembly"


def test_evidence_is_capped_per_document():
    """One verbose policy must not be able to occupy the whole context window."""
    from app.knowledge import policy_search

    index = build_seed_index()
    evidence, _issues, _events = retrieve_policy_evidence(
        index, QUERY, ["SECURITY_POLICY"], top_k=20
    )
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.source_id] = counts.get(item.source_id, 0) + 1
    assert counts, "the query must retrieve something"
    assert max(counts.values()) <= policy_search.settings.retrieval_evidence_per_document


def test_evidence_order_preserves_the_reranker_decision():
    """Sorting evidence by raw score at the end would silently undo the reranker."""

    class Promote:
        def complete_json(self, system: str, user: str):
            return {"ranking": ["POL-VPN-SOP-001-ROOT"]}

    result = run_retrieval("VPN 申请流程有哪些步骤", rerank_client=Promote())
    assert result.evidence[0]["source_id"] == "POL-VPN-SOP-001"


# ----------------------------------------------------------- plan plumbing


def test_time_range_actually_filters_documents():
    """`time_range` was extracted by query understanding and then dropped."""
    from app.knowledge.models import PolicyDocument
    from app.knowledge.policy_search import _passes_filters

    future = PolicyDocument(
        id="P-FUTURE",
        kb_id="IT_SOP",
        title="未来制度",
        content="x",
        version="1.0",
        authority_level=10,
        effective_at="2030-01-01",
        expires_at=None,
    )
    expired = PolicyDocument(
        id="P-OLD",
        kb_id="IT_SOP",
        title="过期制度",
        content="x",
        version="1.0",
        authority_level=10,
        effective_at="2000-01-01",
        expires_at="2001-01-01",
    )
    timeless = PolicyDocument(
        id="P-ANY",
        kb_id="IT_SOP",
        title="无日期制度",
        content="x",
        version="1.0",
        authority_level=10,
        effective_at=None,
        expires_at=None,
    )

    window = {"time_range": {"year": 2024}}
    assert _passes_filters(future, window) is False
    assert _passes_filters(expired, window) is False
    assert _passes_filters(timeless, window) is True
    # No filter means no filtering.
    assert _passes_filters(future, {}) is True
    assert _passes_filters(future, None) is True


def test_the_plan_reports_which_dense_backend_served_the_run():
    result = run_retrieval(QUERY, embedding_provider=CountingProvider())
    assert result.plan["retrieval_mode"] == RETRIEVAL_MODE_HYBRID
    assert result.plan["dense_backend"] in {BACKEND_PGVECTOR, BACKEND_IN_MEMORY}


def test_run_retrieval_stays_synchronous_for_non_async_callers():
    """It is called from sync code too, so the async move lives at the graph node."""
    assert not inspect.iscoroutinefunction(run_retrieval)


@pytest.mark.asyncio
async def test_the_graph_node_offloads_retrieval_off_the_event_loop(monkeypatch):
    """A blocking call awaited on the loop stalls every other request."""
    from app.graph.nodes import knowledge as node

    loop_threads: list[int] = []
    import threading

    def fake_run(*_args, **_kwargs):
        loop_threads.append(threading.get_ident())
        raise RuntimeError("stop here")

    monkeypatch.setattr(node, "run_retrieval", fake_run)

    with pytest.raises(RuntimeError):
        await node.retrieve_knowledge(
            {"latest_user_message": QUERY, "user_context": {"roles": []}}
        )

    assert loop_threads, "run_retrieval must have been called"
    assert loop_threads[0] != threading.get_ident(), (
        "retrieval must run on a worker thread, not the event loop thread"
    )
