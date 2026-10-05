"""Model-chosen tools (function calling).

Tool selection used to be a regex over the user's message. It worked, but it meant the model
never decided anything: it was handed results from a tool a pattern had picked. This module asks
the model instead, with the catalog's JSON Schemas, and returns the calls it wants to make.

Three deliberate constraints:

* **Only direct tools are offered.** Approval-gated tools (currently `ticket_merge`) are never in
  the list. They are reached through the intent taxonomy and the approval gate, so the model can
  never be the reason a destructive action runs.
* **The caller filters candidates first.** `authorize_tool` and the agent's allow-list decide what
  the model is even shown, so it is not tempted to call something it will be refused.
* **A refusal is not fatal.** If there is no API key, the call fails, or the model returns nothing
  usable, the caller falls back to the rule-based selection. The service desk keeps working; it
  just says `tool_routing: rule-based` in `/ready`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from app.config import settings
from app.tools.catalog import openai_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是企业服务台的助手。根据用户这一句话，决定是否需要调用工具。"
    "只有当工具确实能推进用户的问题时才调用；能直接回答就不要调用。"
    "需要创建工单时，title 用用户原话里的关键信息，不要编造编号或人名。"
    "不要调用不在工具列表里的工具。"
)


class FunctionCallingUnavailable(RuntimeError):
    """No usable model, so the caller should fall back to rules."""


@dataclass(frozen=True)
class ToolChoice:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class ModelTurn:
    """One assistant turn: what it wants to call, plus the message to echo back.

    The OpenAI-compatible protocol requires the assistant message carrying ``tool_calls`` to be
    appended verbatim before the ``role: "tool"`` replies, so it is kept rather than
    reconstructed -- a rebuilt message can differ in ways the API rejects.
    """

    calls: list[ToolChoice]
    assistant_message: dict[str, Any]
    content: str | None = None


def function_calling_enabled() -> bool:
    return bool(settings.function_calling_enabled and settings.deepseek_api_key)


def routing_mode() -> str:
    """How tools *would* be chosen: ``model`` when a model is configured, else ``rule-based``."""
    return "model" if function_calling_enabled() else "rule-based"


#: What actually chose the tools the last time a turn ran. Configuration and reality differ
#: whenever the model call fails and the caller falls back, and an operator should be able to see
#: that rather than read "model" and assume it.
_last_observed_mode: str | None = None


def note_routing_mode(mode: str) -> None:
    global _last_observed_mode
    _last_observed_mode = mode


def routing_status() -> dict[str, Any]:
    return {
        "configured": routing_mode(),
        "lastObserved": _last_observed_mode,
        "fellBack": _last_observed_mode == "rule-based" and function_calling_enabled(),
        # The loop is bounded on purpose: an agent that can keep calling tools needs a ceiling on
        # rounds and on total calls, or a confused model becomes an unbounded bill.
        "maxRounds": settings.agent_max_rounds,
        "maxToolCalls": settings.agent_max_tool_calls,
    }


async def choose_tools(
    user_message: str,
    tool_names: list[str],
    *,
    poster: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    conversation_hint: str | None = None,
) -> list[ToolChoice]:
    """One-shot convenience wrapper: ask the model which tools to call for this message.

    The agent loop uses :func:`converse` directly, because it has to carry the running message
    list between rounds.
    """
    if not tool_names:
        return []
    turn = await converse(
        _messages(user_message, conversation_hint), tool_names, poster=poster
    )
    return turn.calls


async def converse(
    messages: list[dict[str, Any]],
    tool_names: list[str],
    *,
    poster: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> ModelTurn:
    """One assistant turn given the whole conversation so far."""
    if not tool_names:
        return ModelTurn(calls=[], assistant_message={})

    payload: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "tools": openai_tools(tool_names),
        # `auto` on purpose: "answer directly" must remain an option, otherwise every turn would
        # raise a ticket even when the user asked a question the assistant can answer.
        "tool_choice": "auto",
        "temperature": 0,
    }

    if poster is not None:
        data = await poster(payload)
    else:
        if not function_calling_enabled():
            raise FunctionCallingUnavailable("no model configured for tool calling")
        data = await _post(payload)

    return _parse_turn(data)


def tool_result_message(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """The `role: "tool"` reply for one call.

    Only the fields the model can act on are sent: whether it worked, the human-readable text,
    the error code, and the data (which carries identifiers it may need next, such as a ticket
    number).
    """
    payload = {
        "success": bool(result.get("success")),
        "text": str(result.get("text") or ""),
    }
    if result.get("error"):
        payload["error"] = result["error"]
    if result.get("data") is not None:
        payload["data"] = result["data"]
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _messages(user_message: str, conversation_hint: str | None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_hint:
        messages.append({"role": "system", "content": conversation_hint})
    messages.append({"role": "user", "content": user_message})
    return messages


async def _post(payload: dict[str, Any]) -> dict[str, Any]:
    base = settings.deepseek_base_url.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        # A model-side failure is the caller's cue to fall back, not an error for the user.
        raise FunctionCallingUnavailable(
            f"tool calling failed with {response.status_code}: {response.text[:200]}"
        )
    return response.json()


def _parse_turn(data: dict[str, Any]) -> ModelTurn:
    """Read one assistant turn out of a chat completion, tolerating anything malformed."""
    if not isinstance(data, dict):
        return ModelTurn(calls=[], assistant_message={})
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ModelTurn(calls=[], assistant_message={})
    message = (choices[0] or {}).get("message") or {}
    if not isinstance(message, dict):
        return ModelTurn(calls=[], assistant_message={})

    content = message.get("content")
    calls = message.get("tool_calls")
    parsed: list[ToolChoice] = []
    if isinstance(calls, list):
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function.get("arguments")
            if isinstance(raw_arguments, dict):
                arguments = raw_arguments
            elif isinstance(raw_arguments, str) and raw_arguments.strip():
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    # A model that emits unparsable arguments has not made a usable request.
                    logger.warning("Discarding tool call %s: unparsable arguments", name)
                    continue
            else:
                arguments = {}
            if not isinstance(arguments, dict):
                continue
            parsed.append(
                ToolChoice(
                    name=name,
                    arguments=arguments,
                    call_id=str(call.get("id") or f"call_{index}_{name}"),
                )
            )

    # Echo back a clean assistant message: the raw one may carry fields the API rejects on the
    # way back in, and a call whose arguments did not parse must not be echoed at all.
    assistant_message: dict[str, Any] = {"role": "assistant"}
    if isinstance(content, str) and content:
        assistant_message["content"] = content
    if parsed:
        assistant_message["content"] = assistant_message.get("content") or None
        assistant_message["tool_calls"] = [
            {
                "id": choice.call_id,
                "type": "function",
                "function": {
                    "name": choice.name,
                    "arguments": json.dumps(choice.arguments, ensure_ascii=False),
                },
            }
            for choice in parsed
        ]
    return ModelTurn(
        calls=parsed,
        assistant_message=assistant_message,
        content=content if isinstance(content, str) else None,
    )
