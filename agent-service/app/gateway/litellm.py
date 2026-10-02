"""DeepSeek chat client built directly on `httpx`.

Model calls use httpx directly so startup does not depend on LiteLLM
initialization or its remote model-cost metadata fetch.

Every call — streaming or not — is measured and its provider-reported usage is
recorded through `app.observability`. The provider is the only source for token
counts here: when a response carries no `usage` object (including an aborted
stream) the call is recorded with ``usage=None``, which increments
``llm.usage_reporting{reported="false"}`` instead of quietly counting zero
tokens. Cost is derived from the price table and left unavailable for a model
that has no verified entry.
"""

import json
import time
from typing import AsyncIterator, Mapping

import httpx

from app.config import settings
from app.observability.tokens import record_provider_usage


# Model calls use httpx directly so startup does not depend on LiteLLM
# initialization or its remote model-cost metadata fetch.
def _completion_url() -> str:
    base_url = settings.deepseek_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        timeout=settings.llm_timeout_seconds,
        connect=min(10.0, settings.llm_timeout_seconds),
    )


def _record(
    model: str,
    usage: Mapping[str, object] | None,
    started: float,
    *,
    outcome: str,
) -> None:
    """Record one model call. Never raises into the request path."""
    record_provider_usage(
        model,
        usage,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        outcome=outcome,
    )


async def stream_completion(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    api_key: str | None = None,
    system: str,
    temperature: float = 0.6,
) -> AsyncIterator[tuple[str, str]]:
    selected_model = model or settings.deepseek_model
    resolved_api_key = api_key or settings.deepseek_api_key

    if not resolved_api_key:
        # The local rule-based path is not a model call, so it is not counted as
        # one; counting it would inflate every cost report with unpriced traffic.
        yield ("answer", deterministic_answer(messages, system))
        return

    started = time.perf_counter()
    usage: Mapping[str, object] | None = None
    outcome = "ok"

    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            async with client.stream(
                "POST",
                _completion_url(),
                headers={
                    "Authorization": f"Bearer {resolved_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "system", "content": system},
                        *messages,
                    ],
                    "temperature": temperature,
                    "stream": True,
                    # Ask DeepSeek for a final usage frame; without this a
                    # streamed call reports no tokens at all.
                    "stream_options": {"include_usage": True},
                },
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    outcome = "error"
                    raise RuntimeError(
                        f"模型服务返回 {response.status_code}: {body[:500]}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(chunk, dict) and chunk.get("usage"):
                        usage = chunk["usage"]

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ("reasoning", str(reasoning))

                    text = delta.get("content")
                    if text:
                        yield ("answer", str(text))
    except httpx.TimeoutException as exc:
        outcome = "timeout"
        raise RuntimeError("模型服务调用超时") from exc
    except httpx.HTTPError as exc:
        outcome = "error"
        raise RuntimeError(f"模型服务连接失败: {exc}") from exc
    except GeneratorExit:
        # The consumer stopped early (client disconnect). Usage was never
        # reported, and the caller is gone, so record and propagate unchanged.
        outcome = "abandoned"
        raise
    finally:
        _record(selected_model, usage, started, outcome=outcome)


async def generate_title(
    prompt: str,
    api_key: str | None = None,
) -> str:
    resolved_api_key = api_key or settings.deepseek_api_key
    if not resolved_api_key:
        return "新对话"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(
                _completion_url(),
                headers={
                    "Authorization": f"Bearer {resolved_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.deepseek_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个擅长总结核心词汇的助手。根据用户输入生成不超过10个字的标题，不要引号或标点。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 20,
                },
            )
            response.raise_for_status()
            payload = response.json()
            text = str(payload["choices"][0]["message"]["content"]).strip()
        _record(settings.deepseek_model, payload.get("usage"), started, outcome="ok")
        return text[:20] or "新对话"
    except Exception:
        _record(settings.deepseek_model, None, started, outcome="error")
        return "新对话"


def deterministic_answer(
    messages: list[dict[str, str]],
    system: str,
) -> str:
    last_user = next(
        (item["content"] for item in reversed(messages) if item["role"] == "user"),
        "",
    )
    return (
        "Agent Service 已接收请求。当前未配置 DEEPSEEK_API_KEY，因此返回本地规则结果。\n\n"
        f"用户请求：{last_user}\n"
        f"系统上下文：{system[-600:]}"
    )
