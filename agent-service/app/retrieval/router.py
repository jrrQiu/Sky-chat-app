from __future__ import annotations

from typing import Any

from app.retrieval.query import QueryPlan
from app.retrieval.types import RetrievalIndex


def _can_access(
    allowed_roles: list[str],
    user_context: dict[str, Any] | None,
) -> bool:
    if not allowed_roles:
        return True
    if not user_context:
        return False
    roles = set(user_context.get("roles", []))
    return bool(roles.intersection(allowed_roles))


def route_knowledge_bases(
    plan: QueryPlan,
    index: RetrievalIndex,
    user_context: dict[str, Any] | None = None,
) -> list[str]:
    requested = plan.kb_scopes or [kb.id for kb in index.knowledge_bases]
    accessible = [
        kb.id
        for kb in index.knowledge_bases
        if kb.id in requested and _can_access(kb.allowed_roles, user_context)
    ]
    return accessible
