from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.knowledge.ingest import index_cache_state
from app.intent.semantic import semantic_classifier_status
from app.observability.tracing import observability_status
from app.tools.adapters import tool_status
from app.retrieval.embedding import describe_dense_channel

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness that reflects the durability contract.

    A service that cannot durably store checkpoints and approvals is not ready:
    it would accept a high-risk request it can never resume.
    """
    backend = getattr(request.app.state, "checkpoint_backend", None)
    graph = getattr(request.app.state, "graph", None)
    durable = backend == "postgres"
    dense_channel = describe_dense_channel()
    checks = {
        "graph": graph is not None,
        "checkpoint_backend": backend,
        "durable_checkpoints": durable,
        "approval_store": "postgres" if settings.database_url else "memory",
        "environment": settings.environment,
        "caller_identity": (
            "signed-jwt" if settings.agent_internal_jwt_secret else "legacy-static"
        ),
        "rate_limiting": settings.rate_limit_enabled,
        "retrieval_rail": settings.retrieval_rail_enabled,
        # Which retrieval channels this deployment will really use, so "hybrid
        # retrieval" is a claim an operator can verify rather than assume.
        "retrieval_mode": dense_channel["mode"],
        "dense_embeddings": dense_channel,
        "index_cache": index_cache_state(),
        # What observability is actually active, so "traced" is verifiable.
        "observability": observability_status(),
        # Whether the intent classifier can really compare meaning. Without an embedding model
        # it is unavailable and the rule/LLM paths decide, which an operator should be able to
        # see rather than infer from a stale design document.
        "intent_semantic": semantic_classifier_status(),
        # Which tools are integrations and which are still placeholders, so nobody has to read
        # the source to find out that IdP/MDM/HRIS/ERP are stubs.
        "tools": tool_status(),
    }
    healthy = graph is not None and (durable or not settings.is_production)
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "degraded", "checks": checks},
    )
