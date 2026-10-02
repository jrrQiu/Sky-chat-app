from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.knowledge.ingest import index_cache_state

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
        "index_cache": index_cache_state(),
    }
    healthy = graph is not None and (durable or not settings.is_production)
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "degraded", "checks": checks},
    )
