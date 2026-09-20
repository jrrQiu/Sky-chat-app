from fastapi import FastAPI

from app.api.routes.approvals import router as approvals_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sky Chat Agent Service",
        version="0.1.0",
        description="FastAPI + LangGraph enterprise service-desk agent service.",
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(approvals_router)

    return app


app = create_app()
