import json
from typing import AsyncIterator

import httpx

from app.config import settings


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
        yield ("answer", deterministic_answer(messages, system))
        return

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
                },
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
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
        raise RuntimeError("模型服务调用超时") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"模型服务连接失败: {exc}") from exc


async def generate_title(
    prompt: str,
    api_key: str | None = None,
) -> str:
    resolved_api_key = api_key or settings.deepseek_api_key
    if not resolved_api_key:
        return "新对话"

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
        return text[:20] or "新对话"
    except Exception:
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
