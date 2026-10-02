"""Operator surface for the knowledge index.

Every knowledge write already drops the cached index through the ingestion path,
but an operator still needs a way to *force* a rebuild: after an out-of-band ACL
change in PostgreSQL, after a migration, or simply to confirm that a deployment is
serving fresh data. Without this the only levers were `INDEX_CACHE_TTL_SECONDS` and
a restart.

Internal-auth only: the route hangs off the same caller dependency every other
internal route uses (`enforce_rate_limit` -> `require_caller`), so the public edge
cannot reach it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.audit import record_action
from app.core.rate_limit import enforce_rate_limit
from app.core.security import Caller
from app.knowledge.ingest import build_seed_index, index_cache_state, invalidate_index

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

# A refresh forces a database read; the limiter keeps a retry loop from hammering
# the system of record.
_refresh_limiter = enforce_rate_limit(
    "knowledge.index.refresh", settings.rate_limit_default_per_minute
)


@router.post("/index/refresh")
async def refresh_knowledge_index(caller: Caller = Depends(_refresh_limiter)):
    """Drop the cached knowledge index and rebuild it now.

    The rebuild is eager and unscoped so the returned `index_cache` state proves the
    refresh took effect: `generation` advanced and the age of the copy that now
    serves requests is near zero.
    """
    invalidate_index()
    build_seed_index(None)
    state = index_cache_state()
    await record_action(
        "knowledge.index.refresh",
        caller=caller,
        object_type="knowledge_index",
        object_id="default",
        detail={
            "generation": state.get("generation"),
            "source": state.get("source"),
            "documents": state.get("documents"),
            "chunks": state.get("chunks"),
        },
    )
    return JSONResponse(
        status_code=200,
        content={"status": "refreshed", "index_cache": state},
    )
