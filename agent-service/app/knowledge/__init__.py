"""Knowledge models, corpus, ingestion, and policy retrieval.

`retrieve_policy_evidence` is re-exported lazily (PEP 562) on purpose. This package
is imported by `app.retrieval.types` (for the index models), while
`app.knowledge.policy_search` imports back into `app.retrieval` through
`app.retrieval.hybrid_search`. An eager re-export here made the import *order*
decide whether the service could start at all, which is not a dependency anyone
should have to remember.
"""

from __future__ import annotations

from typing import Any

__all__ = ["retrieve_policy_evidence"]


def __getattr__(name: str) -> Any:
    if name == "retrieve_policy_evidence":
        from app.knowledge.policy_search import retrieve_policy_evidence

        return retrieve_policy_evidence
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
