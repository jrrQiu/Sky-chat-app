from __future__ import annotations

from typing import Any

from app.intent.taxonomy import enabled_rules
from app.retrieval.embedding import HashEmbeddingProvider, dense_cosine_similarity


class SemanticIntentClassifier:
    def __init__(self, provider=None) -> None:
        self.provider = provider or HashEmbeddingProvider()
        self.examples = _build_examples()

    def classify(self, text: str) -> dict[str, Any] | None:
        if not text.strip():
            return None
        query_vector = self.provider.embed(text)
        if not query_vector:
            return None

        best: dict[str, Any] | None = None
        best_score = -1.0
        for candidate in self.examples:
            score = max(
                (
                    dense_cosine_similarity(query_vector, self.provider.embed(example))
                    for example in candidate["examples"]
                ),
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
                    "source": "semantic",
                }

        return best


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
