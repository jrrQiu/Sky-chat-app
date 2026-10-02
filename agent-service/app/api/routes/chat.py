import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.config import settings
from app.core.audit import record_action
from app.core.idempotency import idempotency_store
from app.core.rate_limit import enforce_rate_limit
from app.core.security import Caller
from app.graph.runner import format_sse, run_graph
from app.persistence.redis_streams import RedisEventStream

router = APIRouter(prefix="/v1/chat", tags=["chat"])

_stream_limiter = enforce_rate_limit("chat.stream", settings.rate_limit_chat_per_minute)


async def _event_generator(request: ChatRequest, graph=None):
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    redis_stream = RedisEventStream()
    terminal_seen = False

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    task = asyncio.create_task(run_graph(request, emit, graph=graph))
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.stream_idle_timeout_seconds,
                )
            except asyncio.TimeoutError:
                error = {
                    "type": "error",
                    "message": "Agent service timed out",
                }
                await redis_stream.publish(f"run:{request.request_id}", error)
                yield format_sse(error)
                break

            await redis_stream.publish(f"run:{request.request_id}", event)
            yield format_sse(event)
            if event.get("type") in {"complete", "approval_required"}:
                terminal_seen = True
                break
    finally:
        if not task.done():
            task.cancel()
        if not terminal_seen:
            complete = {"type": "complete"}
            await redis_stream.publish(f"run:{request.request_id}", complete)
            yield format_sse(complete)


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    http_request: Request,
    caller: Caller = Depends(_stream_limiter),
):
    # `payload` is the pydantic body; the compiled graph lives on the ASGI app
    # state, which is only reachable through the Starlette request.
    graph = getattr(http_request.app.state, "graph", None)
    if graph is None:
        # No graph means no durable checkpoint store; accepting the request would
        # create a high-risk turn that can never be resumed.
        raise HTTPException(status_code=503, detail="GRAPH_NOT_READY")
    idempotency_key = f"agent:{payload.request_id}"
    _, _ = idempotency_store.get_or_create(
        idempotency_key,
        payload.request_id,
    )

    await record_action(
        "chat.stream",
        caller=caller,
        object_type="turn",
        object_id=payload.request_id,
        detail={"conversation_id": payload.conversation_id},
    )

    return StreamingResponse(
        _event_generator(payload, graph=graph),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
