"""Defect 2: the dense channel must be real, or explicitly absent.

`default_embedding_provider()` used to fall back to `HashEmbeddingProvider()` in
silence. Its vectors are a hashed bag of tokens, so their cosine similarity is
noise: the service advertised hybrid retrieval while fusing that noise into every
result, and `/ready` said nothing about it. These tests pin the honest behaviour —
lexical-only when nothing real is configured, an opt-in for the hash stand-in, and
fusion that survives an absent or empty dense channel.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.knowledge.ingest import build_seed_index
from app.main import create_app
from app.retrieval import embedding
from app.retrieval.embedding import (
    HashEmbeddingProvider,
    default_embedding_provider,
    describe_dense_channel,
)
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.hybrid_search import (
    bm25_rank,
    hybrid_retrieve_chunks,
    sparse_rank,
)
from app.retrieval.pipeline import run_retrieval
from app.retrieval.types import RETRIEVAL_MODE_HYBRID, RETRIEVAL_MODE_LEXICAL_ONLY

QUERY = "VPN 外网访问财务系统需要哪些审批和条件？"


class FakeEmbeddingProvider:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class DeadEmbeddingProvider:
    """A configured provider whose call fails, as an HTTP error would."""

    def embed(self, text: str):
        return None


@pytest.fixture
def no_dense_configuration(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "allow_hash_embeddings", False)
    monkeypatch.setattr(settings, "retrieval_use_llm", False)
    monkeypatch.setattr(settings, "retrieval_use_postgres", False)


# ------------------------------------------------------------------ provider


def test_hash_embeddings_are_off_by_default(no_dense_configuration):
    assert settings.allow_hash_embeddings is False
    assert default_embedding_provider() is None, (
        "an unconfigured deployment must not silently receive a hash provider"
    )


def test_hash_embeddings_require_an_explicit_opt_in(monkeypatch, caplog):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "allow_hash_embeddings", True)
    monkeypatch.setattr(embedding, "_warned", set())

    with caplog.at_level(logging.WARNING):
        provider = default_embedding_provider()

    assert isinstance(provider, HashEmbeddingProvider)
    assert any(
        "no semantic signal" in record.message for record in caplog.records
    ), "using the hash stand-in must be loud"


def test_hash_provider_warns_when_constructed_directly(monkeypatch, caplog):
    """The intent classifier constructs it without going through the resolver."""
    monkeypatch.setattr(embedding, "_warned", set())

    with caplog.at_level(logging.WARNING):
        HashEmbeddingProvider()

    assert any(
        "HashEmbeddingProvider is a test/offline stand-in" in record.message
        for record in caplog.records
    )


def test_a_configured_key_still_produces_an_http_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    provider = default_embedding_provider()
    assert provider is not None
    assert not isinstance(provider, HashEmbeddingProvider)
    assert provider.model == settings.embedding_model


# ------------------------------------------------------------- retrieval mode


def test_retrieval_is_lexical_only_and_says_so(no_dense_configuration):
    result = run_retrieval(QUERY)

    assert result.retrieval_mode == RETRIEVAL_MODE_LEXICAL_ONLY
    assert result.plan["retrieval_mode"] == RETRIEVAL_MODE_LEXICAL_ONLY
    assert result.to_dict()["retrieval_mode"] == RETRIEVAL_MODE_LEXICAL_ONLY
    assert result.evidence, "lexical-only retrieval must still return evidence"
    assert all(
        "vector" not in (item["matched_by"] or "") for item in result.evidence
    ), "no result may claim a dense match when no dense channel ran"


def test_retrieval_is_hybrid_when_a_dense_provider_is_supplied():
    result = run_retrieval(QUERY, embedding_provider=FakeEmbeddingProvider())

    assert result.retrieval_mode == RETRIEVAL_MODE_HYBRID
    assert result.plan["retrieval_mode"] == RETRIEVAL_MODE_HYBRID
    assert any(
        "vector" in (item["matched_by"] or "") for item in result.evidence
    ), "a real dense channel must be visible in the fusion provenance"


def test_ready_reports_the_lexical_only_mode(no_dense_configuration):
    with TestClient(create_app()) as client:
        checks = client.get("/ready").json()["checks"]

    assert checks["retrieval_mode"] == RETRIEVAL_MODE_LEXICAL_ONLY
    dense = checks["dense_embeddings"]
    assert dense["mode"] == RETRIEVAL_MODE_LEXICAL_ONLY
    assert dense["provider"] is None
    assert dense["configured"] is False
    assert dense["hash_embeddings_enabled"] is False
    assert dense["model"] is None


def test_ready_reports_hybrid_when_a_provider_is_configured_and_wired(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "retrieval_use_llm", True)

    dense = describe_dense_channel()
    assert dense["mode"] == RETRIEVAL_MODE_HYBRID
    assert dense["provider"] == "http"
    assert dense["configured"] is True
    assert dense["model"] == settings.embedding_model

    monkeypatch.setattr(settings, "retrieval_use_llm", False)
    monkeypatch.setattr(settings, "retrieval_use_postgres", False)
    assert describe_dense_channel()["mode"] == RETRIEVAL_MODE_LEXICAL_ONLY, (
        "a configured key on a deployment that never asks for a provider is not "
        "hybrid retrieval"
    )


# ------------------------------------------------------- fusion without dense


def test_fusion_handles_an_absent_dense_channel():
    """No dense list at all: the lexical channels still produce a ranking."""
    index = build_seed_index()
    with_dense = hybrid_retrieve_chunks(
        QUERY,
        index,
        {"SECURITY_POLICY", "FINANCE_POLICY"},
        embedding_provider=FakeEmbeddingProvider(),
    )
    with_dead_dense = hybrid_retrieve_chunks(
        QUERY,
        index,
        {"SECURITY_POLICY", "FINANCE_POLICY"},
        embedding_provider=DeadEmbeddingProvider(),
    )
    without_dense = hybrid_retrieve_chunks(
        QUERY,
        index,
        {"SECURITY_POLICY", "FINANCE_POLICY"},
    )

    assert without_dense, "a lexical-only run must not return nothing"
    assert with_dense, "an additive dense channel must not break the run"
    assert {candidate.item.id for candidate in without_dense} == {
        candidate.item.id for candidate in with_dead_dense
    }, "a dense channel that yields nothing must not change the lexical result"


def test_reciprocal_rank_fusion_tolerates_empty_channels():
    index = build_seed_index()
    chunks = index.chunks[:20]
    lexical = bm25_rank(QUERY, chunks, top_k=5)
    sparse = sparse_rank(QUERY, chunks, top_k=5)

    with_empty_channel = reciprocal_rank_fusion([lexical, [], sparse], k=60)
    without_empty_channel = reciprocal_rank_fusion([lexical, sparse], k=60)

    assert [candidate.item.id for candidate in with_empty_channel] == [
        candidate.item.id for candidate in without_empty_channel
    ]
    assert reciprocal_rank_fusion([[], []], k=60) == []


def test_lexical_only_mode_is_wired_without_a_provider(no_dense_configuration):
    """What the graph node resolves when embeddings are unconfigured."""
    resolved = default_embedding_provider()
    assert resolved is None
    result = run_retrieval(QUERY, embedding_provider=resolved)
    assert result.plan["retrieval_mode"] == RETRIEVAL_MODE_LEXICAL_ONLY
