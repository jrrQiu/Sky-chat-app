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

import contextvars
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from app.config import settings
from app.tools.catalog import openai_tools
from app.tools.resilience import call_with_retry, policy_for_model_turn

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是企业服务台的助手。根据用户这一句话，决定是否需要调用工具。"
    "只有当工具确实能推进用户的问题时才调用；能直接回答就不要调用。"
    "需要创建工单时，title 用用户原话里的关键信息，不要编造编号或人名。"
    "不要调用不在工具列表里的工具。"
)


def function_calling_model() -> str:
    """The model a tool-selection turn uses.

    One resolver for the whole loop, so the model that chooses the tools and the model
    that writes the answer are the same unless the caller deliberately overrides it. These
    used to diverge: the tool turn read ``settings.deepseek_model`` while the answer used
    ``state["model"]``, so a request could be priced and reasoned about as one model and
    decided by another.
    """
    return (settings.function_calling_model or settings.deepseek_model or "").strip()


#: The caller's model choice for the current turn. A context variable rather than a
#: parameter so the existing call signatures (and their tests) stay unchanged, while the
#: loop still gets to say which model answered.
_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tool_calling_model", default=None
)


def model_override() -> str | None:
    return _model_override.get()


def set_model_override(model: str | None):
    """Set the turn's model. Returns the token to pass to :func:`reset_model_override`."""
    return _model_override.set((model or "").strip() or None)


def reset_model_override(token) -> None:
    _model_override.reset(token)


def resolved_model() -> str:
    return (model_override() or "").strip() or function_calling_model()


class FunctionCallingUnavailable(RuntimeError):
    """No usable model, so the caller should fall back to rules.

    ``status_code`` carries the HTTP status when the provider answered with one. Without it a
    429 looked like a deterministic refusal -- ``is_transient`` had nothing to classify, the
    retry policy never fired, and a single rate-limit response silently degraded the turn to
    rule-based tool selection instead of being retried.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    #: Token usage for this turn, when the provider reported it. Used to bound the loop by cost
    #: as well as by rounds; ``None`` means the provider did not say, which is reported rather
    #: than assumed to be zero.
    usage: dict[str, int] | None = None
    #: How many attempts this turn took (1 = no retry) and why any retry happened. Kept on
    #: the turn so the loop can report "the model needed two tries" instead of hiding it.
    attempts: int = 1
    retry_reasons: tuple[str, ...] = ()
    #: True when the usage above is a local estimate because the provider reported none.
    usage_estimated: bool = False


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
        # rounds, on total calls and on tokens, or a confused model becomes an unbounded bill.
        "maxRounds": settings.agent_max_rounds,
        "maxToolCalls": settings.agent_max_tool_calls,
        "tokenBudget": settings.agent_token_budget,
        "stopAfterWrite": settings.agent_stop_after_write,
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
    """One assistant turn given the whole conversation so far.

    Retried under the model-turn policy (T1/T2): the call is read-only in effect, so a 429,
    a 5xx or a transport failure is worth one more attempt before the caller gives up and
    falls back to rule-based selection. A deterministic 4xx is not retried.
    """
    if not tool_names:
        return ModelTurn(calls=[], assistant_message={})

    payload = _payload(messages, tool_names)
    if not function_calling_enabled() and poster is None:
        raise FunctionCallingUnavailable("no model configured for tool calling")

    async def attempt() -> dict[str, Any]:
        if poster is not None:
            return await poster(payload)
        return await _post(payload)

    outcome = await call_with_retry(
        attempt, policy_for_model_turn(), name="model.tool_choice"
    )
    turn = _parse_turn(outcome.value)
    return ModelTurn(
        calls=turn.calls,
        assistant_message=turn.assistant_message,
        content=turn.content,
        usage=turn.usage,
        attempts=outcome.attempts,
        retry_reasons=tuple(outcome.reasons),
    )


async def converse_stream(
    messages: list[dict[str, Any]],
    tool_names: list[str],
    *,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    streamer: Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]] | None = None,
) -> ModelTurn:
    """Stream one assistant turn, forwarding reasoning as it arrives.

    Tool *selection* used to be invisible: the user saw a generic "thinking" line and then a
    finished answer, because the decision was a non-streaming call. Streaming it means the
    reasoning is shown while it happens, and -- more importantly -- a slow model no longer looks
    like a hung one.

    Tool calls arrive as fragments (name once, arguments in pieces keyed by index), so they are
    assembled here rather than trusted per chunk.
    """
    if not tool_names:
        return ModelTurn(calls=[], assistant_message={})

    payload = _payload(messages, tool_names, stream=True)

    attempts = 1
    retry_reasons: tuple[str, ...] = ()
    if streamer is None:
        if not function_calling_enabled():
            raise FunctionCallingUnavailable("no model configured for tool calling")

        # Retrying a *stream* is only safe before the first frame reaches the caller:
        # once a delta has been forwarded, a second attempt would replay text the user
        # has already seen. So the retry covers opening the connection and validating the
        # response, and nothing after that.
        opened = await call_with_retry(
            lambda: _open_stream(messages, tool_names),
            policy_for_model_turn(),
            name="model.tool_choice_stream",
        )
        attempts = opened.attempts
        retry_reasons = tuple(opened.reasons)
        streamer = opened.value
    elif streamer is None:  # pragma: no cover - unreachable, kept for clarity
        raise FunctionCallingUnavailable("no streamer")

    content_parts: list[str] = []
    fragments: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] | None = None

    async for chunk in streamer(payload):
        if not isinstance(chunk, dict):
            continue
        if isinstance(chunk.get("usage"), dict):
            usage = _usage(chunk["usage"]) or usage
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        if not isinstance(delta, dict):
            continue

        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            content_parts.append(piece)
            if on_delta is not None:
                await on_delta(piece)

        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            index = call.get("index")
            index = index if isinstance(index, int) else len(fragments)
            slot = fragments.setdefault(index, {"id": None, "name": None, "arguments": ""})
            if isinstance(call.get("id"), str):
                slot["id"] = call["id"]
            function = call.get("function") or {}
            if isinstance(function.get("name"), str) and function["name"]:
                slot["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                slot["arguments"] += function["arguments"]

    content = "".join(content_parts) or None
    turn = _turn_from_fragments(fragments, content, usage)
    return ModelTurn(
        calls=turn.calls,
        assistant_message=turn.assistant_message,
        content=turn.content,
        usage=turn.usage,
        attempts=attempts,
        retry_reasons=retry_reasons,
    )


def _turn_from_fragments(
    fragments: dict[int, dict[str, Any]],
    content: str | None,
    usage: dict[str, int] | None,
) -> ModelTurn:
    calls: list[ToolChoice] = []
    for index in sorted(fragments):
        slot = fragments[index]
        name = slot.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw = slot.get("arguments") or ""
        if not raw.strip():
            arguments: dict[str, Any] = {}
        else:
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Discarding streamed tool call %s: unparsable arguments", name)
                continue
        if not isinstance(arguments, dict):
            continue
        calls.append(
            ToolChoice(
                name=name,
                arguments=arguments,
                call_id=str(slot.get("id") or f"call_{index}_{name}"),
            )
        )

    assistant_message: dict[str, Any] = {"role": "assistant"}
    if content:
        assistant_message["content"] = content
    if calls:
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
            for choice in calls
        ]
    return ModelTurn(calls=calls, assistant_message=assistant_message, content=content, usage=usage)


async def _stream(payload: dict[str, Any]):
    """The real streaming request: yields one parsed SSE object per `data:` line."""
    base = settings.deepseek_base_url.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise FunctionCallingUnavailable(
                    f"tool calling failed with {response.status_code}: {body[:200]!r}",
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue


def _payload(
    messages: list[dict[str, Any]],
    tool_names: list[str],
    *,
    stream: bool = False,
) -> dict[str, Any]:
    """The request body for one tool-selection turn.

    ``model`` comes from :func:`resolved_model`, so the turn that chooses tools and the turn
    that writes the answer agree unless the caller said otherwise.
    """
    payload: dict[str, Any] = {
        "model": resolved_model(),
        "messages": messages,
        "tools": openai_tools(tool_names),
        # `auto` on purpose: "answer directly" must remain an option, otherwise every turn would
        # raise a ticket even when the user asked a question the assistant can answer.
        "tool_choice": "auto",
        "temperature": 0,
    }
    if stream:
        payload["stream"] = True
        # Without this the streaming API omits usage entirely, and the loop could only be
        # bounded by counting calls.
        payload["stream_options"] = {"include_usage": True}
    return payload


async def _open_stream(
    messages: list[dict[str, Any]],
    tool_names: list[str],
) -> Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]:
    """Open the streaming connection and prove the model accepted the request.

    Split out so the *retryable* part is exactly "connect and check the status": this
    function is what :func:`call_with_retry` repeats. Iterating the body happens after,
    where a failure can no longer be retried without replaying text to the user.
    """
    payload = _payload(messages, tool_names, stream=True)
    client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
    try:
        request = client.build_request(
            "POST",
            _completion_url(),
            headers=_auth_headers(),
            json=payload,
        )
        response = await client.send(request, stream=True)
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            await response.aclose()
            raise FunctionCallingUnavailable(
                f"tool calling failed with {response.status_code}: {body[:200]}",
                status_code=response.status_code,
            )
    except Exception:
        await client.aclose()
        raise

    async def iterate(_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
        finally:
            await response.aclose()
            await client.aclose()

    return iterate


def _completion_url() -> str:
    base = settings.deepseek_base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


def _usage(raw: dict[str, Any]) -> dict[str, int] | None:
    """Normalise token accounting; absent fields are omitted rather than defaulted to zero."""
    fields = {
        "promptTokens": raw.get("prompt_tokens"),
        "completionTokens": raw.get("completion_tokens"),
        "totalTokens": raw.get("total_tokens"),
    }
    clean = {key: int(value) for key, value in fields.items() if isinstance(value, (int, float))}
    return clean or None


def total_tokens(*turns: ModelTurn) -> int:
    """Tokens *added* by these turns.

    Sums input + output rather than the provider's ``total_tokens``: with a growing prompt,
    each turn's ``total_tokens`` already contains the previous turns' input, so adding them
    together counts the same tokens several times and trips the budget early. Input and output
    per turn are disjoint, so the sum is the real spend.
    """
    total = 0
    for turn in turns:
        usage = turn.usage or {}
        total += int(usage.get("promptTokens") or 0) + int(usage.get("completionTokens") or 0)
    return total


def estimated_tokens(messages: list[dict[str, Any]]) -> int:
    """Local token estimate for a message list, used when the provider reports no usage.

    Delegates to the context engine's counter, so the loop and the prompt budget are metered
    by the same tokenizer. Counting zero here (the old behaviour) made
    ``agent_token_budget`` silently inert whenever the provider omitted ``usage``.
    """
    from app.context.tokenizer import count_messages

    return count_messages(messages)


def trim_result_text(text: str, max_tokens: int) -> tuple[str, bool]:
    """Bound one tool result for the model, keeping both ends.

    The full text is still emitted in the ``tool_result`` event, so trimming the copy that
    goes into the prompt loses nothing an auditor needs -- it only stops "three rounds of a
    20k-character result" from quietly consuming the window.
    """
    if max_tokens <= 0 or not text:
        return text, False
    from app.context.tokenizer import count_text
    from app.context.truncate import truncate_text

    if count_text(text) <= max_tokens:
        return text, False
    return truncate_text(text, max_tokens, "head_tail").text, True


def tool_result_message(
    call_id: str,
    result: dict[str, Any],
    *,
    tool_name: str = "",
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """The `role: "tool"` reply for one call.

    Only the fields the model can act on are sent: whether it worked, the human-readable text,
    the error code, and the whitelisted `data` fields (which carry identifiers it may need
    next, such as a ticket number). The text is bounded by tokens (T6) and the data object is
    projected through the catalog's field list, so a target system cannot pour an arbitrary
    structure into the prompt.
    """
    from app.config import settings
    from app.tools.catalog import result_contract

    limit = settings.tool_result_max_tokens if max_tokens is None else max_tokens
    text, trimmed = trim_result_text(str(result.get("text") or ""), limit)
    payload: dict[str, Any] = {
        "success": bool(result.get("success")),
        "text": text,
    }
    if result.get("error"):
        payload["error"] = result["error"]
        # The model-facing half of the failure contract: the code alone tells it
        # nothing about whether the effect landed or what to do next, and inferring
        # that is exactly the job this code is supposed to do instead.
        try:
            from app.tools.failures import advice_for

            payload["advice"] = advice_for(str(result["error"])).to_dict()
        except Exception:  # pragma: no cover - advice must never break a tool result
            pass
    data = result.get("data")
    if isinstance(data, dict):
        _, allowed = result_contract(tool_name)
        if allowed:
            payload["data"] = {key: data[key] for key in allowed if key in data}
        elif not tool_name:
            payload["data"] = data
    if trimmed:
        payload["textTruncated"] = True
        payload["textOriginalChars"] = len(str(result.get("text") or ""))
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
            f"tool calling failed with {response.status_code}: {response.text[:200]}",
            status_code=response.status_code,
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
