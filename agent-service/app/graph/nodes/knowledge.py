from typing import Any

from langchain_core.runnables import RunnableConfig

from app.knowledge.base import (
    format_knowledge_context,
    search_historical_tickets,
    search_knowledge,
)


async def retrieve_knowledge(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    query = str(state.get("latest_user_message", ""))
    include_history = state.get("risk_level") != "low"
    user_context = state.get("user_context")
    documents = search_knowledge(query, user_context=user_context)
    tickets = (
        search_historical_tickets(query, user_context=user_context)
        if include_history
        else []
    )

    emit = config.get("configurable", {}).get("emit") if config else None
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "Knowledge Agent 正在检索制度、SOP 和历史工单。",
                "step": True,
            }
        )

    return {
        "retrieved_documents": [
            {"id": doc.id, "title": doc.title, "content": doc.content}
            for doc in [*documents, *tickets]
        ],
        "knowledge_context": format_knowledge_context(
            query,
            include_history,
            user_context=user_context,
        ),
    }
