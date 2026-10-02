from __future__ import annotations

from typing import Any

from app.retrieval.query import QueryPlan


def build_fault_signature(
    query: str,
    plan: QueryPlan,
) -> dict[str, Any]:
    entities = plan.entities
    return {
        "product": (
            entities.get("products")[0]
            if entities.get("products")
            else "VPN"
        ),
        "component": (
            entities.get("components")[0]
            if entities.get("components")
            else None
        ),
        "environment": entities.get("environment"),
        "error_codes": list(entities.get("error_codes", [])),
        "symptom": query.strip(),
        "recent_change": None,
    }


def signature_text(signature: dict[str, Any]) -> str:
    parts = [
        str(signature.get("product", "")),
        str(signature.get("component", "")),
        str(signature.get("environment", "")),
        " ".join(signature.get("error_codes", [])),
        str(signature.get("symptom", "")),
    ]
    return " ".join(part for part in parts if part)
