from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.approvals import router as approvals_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.workflows import router as workflows_router
from app.config import settings
from app.core.security import internal_auth_problem
from app.graph.checkpoint import open_checkpointer
from app.graph.root import build_root_graph
from app.persistence.postgres_repository import close_pool
from app.services.resume_reconciler import resume_reconciler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to serve in production without a verifiable caller identity: with the
    # legacy static token the agent service would accept any X-User-ID.
    auth_problem = internal_auth_problem()
    if auth_problem:
        raise RuntimeError(auth_problem)

    # `open_checkpointer` fails the startup when a durable backend is required but
    # unreachable, instead of degrading to a process-local checkpointer.
    async with open_checkpointer() as checkpointer:
        app.state.checkpoint_backend = settings.checkpoint_backend
        app.state.graph = build_root_graph(checkpointer)
        resume_reconciler.start(lambda: getattr(app.state, "graph", None))
        try:
            yield
        finally:
            await resume_reconciler.stop()
            await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sky Chat Agent Service",
        version="0.1.0",
        description="FastAPI + LangGraph enterprise service-desk agent service.",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(approvals_router)
    app.include_router(workflows_router)

    return app


app = create_app()
