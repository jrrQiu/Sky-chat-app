import os

import pytest

from app.knowledge.ingest import _build_in_memory_index
from app.persistence.knowledge_repository import PostgresKnowledgeRepository


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is not set",
)
def test_postgres_seed_and_load_roundtrip():
    repository = PostgresKnowledgeRepository(os.environ["TEST_DATABASE_URL"])
    repository.ensure_schema()
    repository.seed(_build_in_memory_index())

    loaded = repository.load_index()

    assert len(loaded.knowledge_bases) == 4
    assert len(loaded.policy_documents) == 7
    assert len(loaded.chunks) == 26
    assert len(loaded.tickets) == 4
    assert len(loaded.events) == 9
