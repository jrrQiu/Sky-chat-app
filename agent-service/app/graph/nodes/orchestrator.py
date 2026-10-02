from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.intent.resolver import resolve_intent
from app.retrieval.llm import LLMStructuredClient


def classify_request(
    text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return resolve_intent(text, context).legacy_dict()


async def orchestrate(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    text = str(state.get("latest_user_message", ""))
    llm_client = LLMStructuredClient() if settings.intent_use_llm else None
    result = resolve_intent(text, state, llm_client=llm_client)
    emit = config.get("configurable", {}).get("emit") if config else None

    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    f"Orchestrator 已识别 domain={result.domain}，"
                    f"operation={result.operation}，intent={result.intent}，"
                    f"confidence={result.confidence:.2f}，source={result.source}。"
                ),
                "step": True,
            }
        )

    action = "clarify" if result.requires_clarification else result.action
    selected_agent = "knowledge" if result.requires_clarification else result.agent_id
    route = (
        "knowledge"
        if selected_agent == "knowledge" or result.requires_clarification
        else "service"
    )

    return {
        "intent": result.intent,
        "domain": result.domain,
        "operation": result.operation,
        "intent_confidence": result.confidence,
        "intent_source": result.source,
        "intent_resolution": result.to_dict(),
        "action": action,
        "selected_agent": selected_agent,
        "risk_level": result.risk,
        "plan": result.plan,
        "route": route,
        "slots": result.slots,
        "missing_fields": result.missing_slots,
        "requires_clarification": result.requires_clarification,
        "clarifying_question": result.clarifying_question,
        "status": "running",
    }
