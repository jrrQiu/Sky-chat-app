from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.retrieval.types import ScoredItem


def reciprocal_rank_fusion(
    ranked_lists: list[list[ScoredItem]],
    k: int = 60,
) -> list[ScoredItem]:
    fused: dict[Any, ScoredItem] = {}
    items: dict[Any, Any] = {}
    sources: dict[Any, set[str]] = defaultdict(set)

    for ranked in ranked_lists:
        for position, candidate in enumerate(ranked, start=1):
            if candidate.score <= 0:
                continue
            key = getattr(candidate.item, "id", candidate.item)
            items[key] = candidate.item
            sources[key].add(candidate.matched_by)
            contribution = 1.0 / (k + position)
            if key not in fused:
                fused[key] = ScoredItem(
                    item=candidate.item,
                    score=contribution,
                    matched_by=candidate.matched_by,
                )
            else:
                fused[key].score += contribution

    merged: list[ScoredItem] = []
    for key, candidate in fused.items():
        merged.append(
            ScoredItem(
                item=items[key],
                score=candidate.score,
                matched_by=",".join(sorted(sources[key])),
            )
        )

    merged.sort(key=lambda candidate: candidate.score, reverse=True)
    for rank, candidate in enumerate(merged, start=1):
        candidate.rank = rank
    return merged


def weighted_fusion(
    ranked_lists: list[tuple[list[ScoredItem], float]],
) -> list[ScoredItem]:
    fused: dict[Any, ScoredItem] = {}
    items: dict[Any, Any] = {}
    sources: dict[Any, set[str]] = defaultdict(set)

    for ranked, weight in ranked_lists:
        for position, candidate in enumerate(ranked, start=1):
            key = getattr(candidate.item, "id", candidate.item)
            items[key] = candidate.item
            sources[key].add(candidate.matched_by)
            contribution = candidate.score * weight / max(position, 1)
            if key not in fused:
                fused[key] = ScoredItem(
                    item=candidate.item,
                    score=contribution,
                    matched_by=candidate.matched_by,
                )
            else:
                fused[key].score += contribution

    merged = [
        ScoredItem(
            item=items[key],
            score=candidate.score,
            matched_by=",".join(sorted(sources[key])),
        )
        for key, candidate in fused.items()
    ]
    merged.sort(key=lambda candidate: candidate.score, reverse=True)
    for rank, candidate in enumerate(merged, start=1):
        candidate.rank = rank
    return merged
