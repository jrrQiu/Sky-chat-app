import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.retrieval.llm import LLMStructuredClient
from app.retrieval.embedding import default_embedding_provider
from app.retrieval.pipeline import run_retrieval


async def retrieve_knowledge(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    query = str(state.get("latest_user_message", ""))
    if state.get("action") == "capabilities":
        return {
            "knowledge_documents": [],
            "retrieved_documents": [],
            "knowledge_context": "",
            "retrieval_plan": {},
            "evidence": [],
            "retrieval_issues": [],
        }

    if state.get("action") == "clarify":
        return {
            "knowledge_documents": [],
            "retrieved_documents": [],
            "knowledge_context": "",
            "retrieval_plan": {},
            "evidence": [],
            "retrieval_issues": [],
        }

    user_context = state.get("user_context")
    query_client = LLMStructuredClient() if settings.retrieval_use_llm else None
    rerank_client = query_client if settings.retrieval_use_llm else None
    embedding_provider = (
        default_embedding_provider()
        if settings.retrieval_use_llm or settings.retrieval_use_postgres
        else None
    )
    # `run_retrieval` is synchronous and does real I/O: a database read to build the
    # index, a vector search, and embedding calls. Awaiting it directly would run all
    # of that *on* the event loop, stalling every other in-flight request for the
    # duration — the failure mode is not "this request is slow" but "all requests are".
    # The retrieval layer stays synchronous (it is also called from non-async code);
    # it is moved to a worker thread here instead.
    result = await asyncio.to_thread(
        run_retrieval,
        query,
        user_context=user_context,
        query_client=query_client,
        rerank_client=rerank_client,
        request_id=state.get("request_id"),
        embedding_provider=embedding_provider,
    )

    emit = config.get("configurable", {}).get("emit") if config else None
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    "Knowledge Agent 已完成查询理解、知识库路由和证据门控，"
                    f"任务类型为 {result.task_type}。"
                ),
                "step": True,
            }
        )

    for event in result.tool_events:
        if emit:
            await emit(event)

    return {
        "knowledge_documents": result.documents,
        "retrieved_documents": result.documents,
        "knowledge_context": result.context,
        "retrieval_plan": result.plan,
        "evidence": result.evidence,
        "retrieval_issues": result.issues,
        "quarantined_evidence_count": len(result.rail_blocked),
        "guardrail_events": result.rail_blocked,
    }
