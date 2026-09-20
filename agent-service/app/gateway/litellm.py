import asyncio
from typing import AsyncIterator

from app.config import settings


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

    import litellm

    response = await litellm.acompletion(
        model=selected_model,
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        api_key=resolved_api_key,
        api_base=settings.deepseek_base_url,
        stream=True,
    )

    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield ("reasoning", reasoning)
        text = getattr(delta, "content", None)
        if text:
            yield ("answer", text)
        await asyncio.sleep(0)


async def generate_title(
    prompt: str,
    api_key: str | None = None,
) -> str:
    resolved_api_key = api_key or settings.deepseek_api_key
    if not resolved_api_key:
        return "新对话"

    try:
        import litellm

        response = await litellm.acompletion(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个擅长总结核心词汇的助手。根据用户输入生成不超过10个字的标题，不要引号或标点。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=20,
            api_key=resolved_api_key,
            api_base=settings.deepseek_base_url,
        )
        text = response.choices[0].message.content.strip()
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
