"""Defect 1: the knowledge index must be invalidated by the write path, not by tests.

Before this, `invalidate_index()` had no production caller at all: a knowledge
update or an ACL revocation stayed invisible until the TTL expired, and an operator
had no lever short of a restart. These tests pin the two callers that now exist —
the ingestion entry point and the internal refresh endpoint — and the readiness
detail an operator uses to judge staleness.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.knowledge import ingest
from app.main import create_app
from tests.internal_auth import auth_headers, mint_token


class RecordingRepository:
    """Duck-typed stand-in for the Postgres repository (no database in unit tests)."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.seeded: list[object] = []
        self.schema_ensured = 0

    def is_available(self) -> bool:
        return self.available

    def ensure_schema(self) -> None:
        self.schema_ensured += 1

    def seed(self, index) -> None:
        self.seeded.append(index)


@pytest.fixture(autouse=True)
def _clean_cache():
    ingest.invalidate_index()
    yield
    ingest.invalidate_index()


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


# ------------------------------------------------ invalidation is part of the write


def test_ingestion_invalidates_the_cached_index():
    """A write path that leaves a cached copy behind serves revoked ACLs."""
    cached = ingest.build_seed_index()
    assert ingest.index_cache_state()["cached"] is True

    repository = RecordingRepository()
    written = ingest.ingest_knowledge_index(cached, repository=repository)

    assert written is True
    assert repository.seeded == [cached]
    assert repository.schema_ensured == 1
    assert ingest.index_cache_state()["cached"] is False, (
        "the cache must not outlive the data it was built from"
    )


def test_ingestion_without_a_system_of_record_writes_nothing():
    repository = RecordingRepository(available=False)
    assert ingest.ingest_knowledge_index(ingest.build_seed_index(), repository) is False
    assert repository.seeded == []


def test_index_built_before_a_concurrent_write_is_not_cached(monkeypatch):
    """The rebuild that lost a race must not republish stale data for a full TTL."""
    real_build = ingest._build_in_memory_index

    def build_with_concurrent_write():
        ingest.invalidate_index()
        return real_build()

    monkeypatch.setattr(ingest, "_build_in_memory_index", build_with_concurrent_write)
    stale = ingest.build_seed_index()

    assert stale is not None
    state = ingest.index_cache_state()
    assert state["cached"] is False
    assert state["source"] is None


# ------------------------------------------------------------- refresh endpoint


def test_refresh_endpoint_rejects_an_unauthenticated_caller(client):
    response = client.post("/v1/knowledge/index/refresh")
    assert response.status_code == 401


def test_refresh_endpoint_rejects_a_forged_token(client):
    headers = auth_headers("ops1", ["admin"])
    headers["Authorization"] = "Bearer " + mint_token(
        "ops1", ["admin"], secret="not-the-shared-secret"
    )
    assert client.post("/v1/knowledge/index/refresh", headers=headers).status_code == 401


def test_refresh_endpoint_rebuilds_the_index_for_an_internal_caller(client):
    before = ingest.build_seed_index()
    generation_before = ingest.index_cache_state()["generation"]

    response = client.post(
        "/v1/knowledge/index/refresh",
        headers=auth_headers("ops1", ["admin"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refreshed"
    state = body["index_cache"]
    assert state["cached"] is True
    assert state["generation"] == generation_before + 1
    assert state["age_seconds"] is not None and state["age_seconds"] < 5
    assert state["documents"] > 0
    assert state["chunks"] > 0
    assert state["source"] == "memory"
    # The refresh really replaced the cached object rather than returning it.
    assert ingest.build_seed_index() is not before


# ------------------------------------------------------------- readiness detail


def test_ready_reports_index_cache_staleness_detail(client):
    ingest.build_seed_index()
    checks = client.get("/ready").json()["checks"]
    cache = checks["index_cache"]

    assert cache["cached"] is True
    assert cache["age_seconds"] is not None
    assert cache["ttl_seconds"] == settings.index_cache_ttl_seconds
    assert 0 <= cache["expires_in_seconds"] <= settings.index_cache_ttl_seconds
    assert cache["role_scoped"] is False
    assert cache["source"] == "memory"
    assert cache["documents"] > 0
    assert cache["chunks"] >= cache["documents"]
    assert cache["generation"] >= 1


def test_index_cache_state_keeps_its_original_keys():
    ingest.invalidate_index()
    state = ingest.index_cache_state()
    assert state["cached"] is False
    assert state["age_seconds"] is None
    assert state["expires_in_seconds"] is None
    assert state["ttl_seconds"] == settings.index_cache_ttl_seconds
    assert state["role_scoped"] is False
