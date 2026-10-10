"""P1 consistency: no channel may claim a capability it does not have.

The retrieval pipeline was fixed to stop faking a dense channel. These tests pin the
three lookalikes that shared the same defect class but lived in other modules: the
"semantic" intent classifier defaulting to hashed token vectors, ticket evidence
labelling a sparse cosine as a vector match, and a direct repository write bypassing
index invalidation.
"""

from __future__ import annotations

from app.config import settings
from app.intent.semantic import (
    SOURCE_SEMANTIC,
    SOURCE_TOKEN_OVERLAP,
    SemanticIntentClassifier,
)
from app.knowledge.ingest import index_cache_state, invalidate_index
from app.retrieval.embedding import HashEmbeddingProvider


class StubProvider:
    """A real-provider stand-in: deterministic, no network, and not a hash provider."""

    def embed(self, text: str) -> list[float]:
        # Two dimensions keyed off simple features, so identical text yields identical vectors.
        return [float(len(text) % 7), float(sum(ord(c) for c in text) % 11)]


# --------------------------------------------------------------------------- intent


def test_intent_classifier_is_unavailable_without_a_provider(monkeypatch):
    # Must be explicit: a developer who has configured EMBEDDING_API_KEY in `.env`
    # would otherwise get a real provider resolved from configuration, and this test
    # would be asserting about a state it never actually created.
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "allow_hash_embeddings", False)
    classifier = SemanticIntentClassifier(provider=None)

    assert classifier.available is False
    # Refusing to answer is the point: the rule and LLM paths then decide, instead of a hashed
    # bag of tokens being presented as semantic understanding.
    assert classifier.classify("帮我开通 VPN 权限") is None


def test_hash_provider_is_labelled_token_overlap_not_semantic(monkeypatch):
    monkeypatch.setattr(settings, "allow_hash_embeddings", True)
    classifier = SemanticIntentClassifier(provider=HashEmbeddingProvider())

    assert classifier.available is True
    assert classifier.source == SOURCE_TOKEN_OVERLAP

    result = classifier.classify("帮我开通 VPN 外网访问权限")
    if result is not None:
        # Whatever it matched, it must not claim to be a semantic match.
        assert result["source"] == SOURCE_TOKEN_OVERLAP
        assert result["source"] != SOURCE_SEMANTIC


def test_a_real_provider_is_labelled_semantic():
    classifier = SemanticIntentClassifier(provider=StubProvider())

    assert classifier.available is True
    assert classifier.source == SOURCE_SEMANTIC
    result = classifier.classify("帮我开通 VPN 外网访问权限")
    if result is not None:
        assert result["source"] == SOURCE_SEMANTIC


def test_example_vectors_are_embedded_once_per_classifier():
    """The taxonomy is static, so re-embedding it per call was wasted work (and network I/O)."""
    calls = {"n": 0}

    class CountingProvider:
        def embed(self, text: str) -> list[float]:
            calls["n"] += 1
            return [float(len(text))]

    classifier = SemanticIntentClassifier(provider=CountingProvider())
    classifier.classify("第一次")
    first_pass = calls["n"]
    classifier.classify("第二次")

    # The second call embeds only the new query, not the whole example set again.
    assert calls["n"] == first_pass + 1


# --------------------------------------------------------------------------- tickets


def test_ticket_evidence_labels_a_sparse_match_as_sparse():
    from app.knowledge.ingest import build_seed_index
    from app.tickets.similar_search import search_similar_tickets

    index = build_seed_index(frozenset({"employee", "network_admin", "it_staff", "admin"}))
    ranked = search_similar_tickets(
        index,
        {"error_codes": [], "product": None, "component": None},
        "VPN 无法连接 认证失败",
        user_context={"userId": "u1", "roles": ["it_staff"]},
        top_k=5,
    )

    labels = {label for item in ranked for label in (item.matched_by or "").split(",") if label}
    assert "vector" not in labels, f"sparse cosine still labelled as a vector match: {labels}"


# --------------------------------------------------------------------------- repository


class _FakeCursor:
    def __init__(self) -> None:
        self.executed = 0

    def execute(self, *_args, **_kwargs) -> None:
        self.executed += 1

    def fetchall(self):
        # `seed` now reads the pre-existing chunk contents first, so it can skip
        # re-embedding text that did not change. An empty pre-state is a valid
        # answer: every chunk is new.
        return []

    def executemany(self, *_args, **_kwargs) -> None:
        self.executed += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, **_kwargs) -> _FakeCursor:
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def test_a_direct_repository_write_invalidates_the_cached_index(monkeypatch):
    """A caller that reaches the repository directly must not leave a stale cache behind."""
    from app.persistence import knowledge_repository as module

    invalidations = {"n": 0}

    def counting_invalidate() -> None:
        invalidations["n"] += 1

    # The repository imports the function lazily inside `seed`, so patch it at the source.
    import app.knowledge.ingest as ingest

    monkeypatch.setattr(ingest, "invalidate_index", counting_invalidate)

    repository = module.PostgresKnowledgeRepository()
    cursor = _FakeCursor()
    monkeypatch.setattr(repository, "_connect", lambda: _FakeConnection(cursor))

    from app.knowledge.ingest import build_seed_index

    index = build_seed_index(frozenset({"employee"}))
    repository.seed(index)

    assert cursor.executed > 0, "the seed must actually write"
    assert invalidations["n"] == 1, "a write must invalidate the cached index exactly once"


def test_invalidate_index_actually_clears_the_cache():
    from app.knowledge.ingest import build_seed_index

    index = build_seed_index(frozenset({"employee"}))
    invalidate_index()
    assert index_cache_state().get("cached") is False
    assert index is not None
