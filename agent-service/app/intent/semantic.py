from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import settings
from app.intent.taxonomy import enabled_rules
from app.retrieval.embedding import (
    HashEmbeddingProvider,
    default_embedding_provider,
    dense_cosine_similarity,
)

#: Reported when a real embedding model produced the vectors, i.e. the match is semantic.
SOURCE_SEMANTIC = "semantic"
#: Reported when the development-only hash provider produced them. The match is then token
#: overlap between bags of words, which is a weaker signal wearing a stronger name if we report
#: it as semantic.
SOURCE_TOKEN_OVERLAP = "token-overlap"


class SemanticIntentClassifier:
    """Nearest-example intent matching over the taxonomy's example phrasings.

    Which provider is used decides whether this deserves the name "semantic":

    * a configured embedding model — yes, it compares meaning;
    * the hash provider (``ALLOW_HASH_EMBEDDINGS=true``, development only) — no, it compares
      token overlap, so results are labelled :data:`SOURCE_TOKEN_OVERLAP`;
    * nothing configured — the classifier is unavailable and :meth:`classify` returns ``None``,
      leaving the rule and LLM paths to decide. Refusing to answer is the honest option: what
      used to be here was a silent fallback to hashed token vectors reported as ``"semantic"``.
    """

    def __init__(self, provider: Any | None = None) -> None:
        if provider is not None:
            resolved = provider
        else:
            resolved = default_embedding_provider()
            if resolved is None and getattr(settings, "allow_hash_embeddings", False):
                resolved = HashEmbeddingProvider()
        self.provider = resolved
        self.uses_hash_provider = isinstance(resolved, HashEmbeddingProvider)
        self.examples = _build_examples()
        self._vectors: list[tuple[dict[str, Any], list[list[float]]]] | None = None

    @property
    def available(self) -> bool:
        return self.provider is not None

    @property
    def source(self) -> str:
        return SOURCE_TOKEN_OVERLAP if self.uses_hash_provider else SOURCE_SEMANTIC

    def classify(self, text: str) -> dict[str, Any] | None:
        if self.provider is None or not text.strip():
            return None
        query_vector = self.provider.embed(text)
        if not query_vector:
            return None

        best: dict[str, Any] | None = None
        best_score = -1.0
        for candidate, vectors in self._candidate_vectors():
            score = max(
                (dense_cosine_similarity(query_vector, vector) for vector in vectors),
                default=0.0,
            )
            if score > best_score:
                best_score = score
                best = {
                    "domain": candidate["domain"],
                    "operation": candidate["operation"],
                    "intent": candidate["intent"],
                    "agent_id": candidate["agent_id"],
                    "action": candidate["action"],
                    "plan": list(candidate["plan"]),
                    "confidence": round(0.55 + 0.40 * score, 4),
                    "source": self.source,
                }

        return best

    def _candidate_vectors(self) -> list[tuple[dict[str, Any], list[list[float]]]]:
        """Example vectors, embedded once per classifier instance.

        The taxonomy is fixed for the life of the process, so embedding every example on every
        request was pure waste — and with a real HTTP provider it was a network round trip per
        example per request. The resolver keeps one classifier alive, so this happens once.
        """
        if self._vectors is None:
            self._vectors = [
                (
                    candidate,
                    [vector for vector in
                     (self.provider.embed(example) for example in candidate["examples"])
                     if vector],
                )
                for candidate in self.examples
            ]
        return self._vectors


@lru_cache(maxsize=1)
def _build_examples() -> list[dict[str, Any]]:
    return [
        {
            "domain": rule["domain"],
            "operation": rule["operation"],
            "intent": rule["intent"],
            "agent_id": rule["agent_id"],
            "action": rule["action"],
            "plan": list(rule.get("plan", [])),
            "examples": [str(example) for example in rule.get("examples", [])],
        }
        for rule in enabled_rules()
    ]


def semantic_classifier_status() -> dict[str, Any]:
    """Operator-facing state for the readiness payload."""
    classifier = get_semantic_classifier()
    return {
        "available": classifier.available,
        "source": classifier.source if classifier.available else None,
        "examples": len(classifier.examples),
    }


@lru_cache(maxsize=1)
def get_semantic_classifier() -> SemanticIntentClassifier:
    """The process-wide classifier, so the example vectors are embedded once."""
    return SemanticIntentClassifier()
