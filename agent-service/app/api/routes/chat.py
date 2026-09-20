import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.core.idempotency import idempotency_store
from app.core.security import require_internal_token
from app.graph.runner import format_sse, run_graph
from app.persistence.redis_streams import RedisEventStream

router = APIRouter(prefix="/v1/chat", tags=["chat"])


async def _event_generator(request: ChatRequest):
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    redis_stream = RedisEventStream()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    task = asyncio.create_task(run_graph(request, emit))
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=120)
            except asyncio.TimeoutError:
                yield format_sse(
                    {"type": "error", "message": "Agent service timed out"}
                )
                break

            await redis_stream.publish(f"run:{request.request_id}", event)
            yield format_sse(event)
            if event.get("type") == "complete":
                break
    finally:
        if not task.done():
            task.cancel()


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    _: str = Depends(require_internal_token),
):
    idempotency_key = f"agent:{request.request_id}"
    _, _ = idempotency_store.get_or_create(
        idempotency_key,
        request.request_id,
    )

    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
