"""Rolling conversation summary: the long-context half of the design.

The previous implementation was not a summary. `ConversationSummaryService.roll`
took the last user message and the last assistant message, joined them with
"用户：…；助手：…", and wrote that as a **new version every turn**. The effect was
the opposite of what a summary is for: everything before the newest two messages
was lost permanently, and the "summary" that remained held exactly the messages
the raw window already contained.

This module produces a summary *of the range the budget planner evicted*:

* triggered by the planner reporting evicted messages, or a prompt above the
  watermark (``report.needs_summary``);
* input is the previous summary plus exactly the evicted messages, so the range
  stays contiguous — ``covered_from``/``covered_until`` are carried through and a
  non-contiguous range is refused explicitly;
* output is structured (``decisions``/``open_items``/``facts``/``preferences``/
  ``open_approvals``) so it renders into the small ``conversation_summary`` block
  instead of growing without bound;
* **a failure never blocks the answer.** With no model, or on a malformed
  response, the fields come from a deterministic extractor and the delta is marked
  ``quality="fallback"`` so the degradation is visible rather than silent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.context.report import SummaryDelta, SummaryRequest
from app.context.tokenizer import count_text
from app.context.truncate import truncate_text

logger = logging.getLogger(__name__)

#: Fields the summary always carries. Fixed so the renderer is a pure function of
#: the fields and the Java side can persist them without guessing a schema.
SUMMARY_FIELDS = (
    "decisions",
    "open_items",
    "facts",
    "preferences",
    "open_approvals",
)

#: Upper bound on the summary's own size. The `conversation_summary` block has its
#: own budget, but bounding it here stops the model from returning an essay the
#: block then has to fight for.
MAX_SUMMARY_TOKENS = 600

#: How much evicted material is sent to the summariser.
MAX_SUMMARY_INPUT_TOKENS = 4_000

SUMMARY_SYSTEM_PROMPT = (
    "你是企业服务台的会话压缩助手。把给定的对话片段压缩成结构化 JSON，"
    "只保留对后续对话有用的信息。不要编造未出现的事实。"
    "只输出 JSON，不要解释、不要代码块。"
)

SUMMARY_SCHEMA_HINT = (
    '{"decisions": [], "open_items": [], "facts": [], "preferences": [], "open_approvals": []}'
)


@dataclass
class SummaryOutcome:
    delta: SummaryDelta
    used_model: bool
    usage: dict[str, Any] | None = None
    prompt_version: str = "summary-v1"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalise_fields(payload: Any) -> dict[str, list[str]]:
    """Coerce anything model-shaped into the fixed field set."""
    if not isinstance(payload, dict):
        return {name: [] for name in SUMMARY_FIELDS}
    return {name: _as_list(payload.get(name)) for name in SUMMARY_FIELDS}


def render_fields(fields: dict[str, Any]) -> str:
    """Render the fields as the short text the summary block carries."""
    labels = {
        "decisions": "已决定",
        "open_items": "待办",
        "facts": "事实",
        "preferences": "偏好",
        "open_approvals": "待审批",
    }
    lines: list[str] = []
    for name in SUMMARY_FIELDS:
        values = _as_list((fields or {}).get(name))
        if not values:
            continue
        lines.append(f"{labels[name]}：" + "；".join(values))
    return "\n".join(lines)


def fallback_fields(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Deterministic extractor used when no model is available.

    Not a summary in the semantic sense, and it says so via ``quality="fallback"``:
    it keeps the questions that were asked and the answers that were given, in
    order, which is strictly more than the old behaviour of discarding everything
    before the newest two messages.
    """
    facts: list[str] = []
    open_items: list[str] = []
    for item in messages:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        compact = truncate_text(content, 64, "tail").text.replace("\n", " ")
        if item.get("role") == "user":
            open_items.append(f"用户曾问：{compact}")
        elif item.get("role") == "assistant":
            facts.append(f"助手曾答：{compact}")
    return {
        "decisions": [],
        "open_items": open_items[:5],
        "facts": facts[:5],
        "preferences": [],
        "open_approvals": [],
    }


def messages_between(
    all_messages: list[dict[str, Any]],
    from_id: str,
    until_id: str,
) -> list[dict[str, Any]]:
    """The inclusive slice between two message ids, empty when not contiguous.

    Returning empty for an unknown anchor is deliberate: summarising the wrong
    range would silently mis-state the conversation, whereas refusing to
    summarise only costs a fallback that says so.
    """
    ids = [str(item.get("id") or "") for item in all_messages]
    if from_id not in ids or until_id not in ids:
        return []
    start = ids.index(from_id)
    end = ids.index(until_id)
    if end < start:
        return []
    return all_messages[start : end + 1]


def _material(
    previous_summary: dict[str, Any],
    evicted: list[dict[str, Any]],
    budget: int,
) -> str:
    pieces: list[str] = []
    previous_text = render_fields(previous_summary.get("fields") or {})
    if previous_text:
        pieces.append("【上一版摘要】\n" + previous_text)
    conversation = []
    for item in evicted:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            conversation.append(f"{role}：{content}")
    if conversation:
        pieces.append("【新增对话片段】\n" + "\n".join(conversation))
    material = "\n\n".join(pieces)
    if count_text(material) <= budget:
        return material
    # Trim the tail: the newest turns matter most, and the previous summary
    # carries the older decisions.
    return truncate_text(material, budget, "tail").text


async def _summarise_with_model(
    material: str,
    *,
    model: str,
    api_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    base_url = settings.deepseek_base_url.rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"请输出如下结构的 JSON：{SUMMARY_SCHEMA_HINT}\n\n"
                    f"待压缩内容：\n{material}"
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_SUMMARY_TOKENS,
    }
    timeout = httpx.Timeout(
        timeout=min(20.0, settings.llm_timeout_seconds),
        connect=min(5.0, settings.llm_timeout_seconds),
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning("Summary model call failed with %s", response.status_code)
            return None, None
        data = response.json()
    except Exception:
        logger.warning("Summary model call failed", exc_info=True)
        return None, None

    usage = data.get("usage") if isinstance(data, dict) else None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, usage if isinstance(usage, dict) else None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Summary model returned unparseable JSON; using the fallback extractor")
        return None, usage if isinstance(usage, dict) else None
    return (parsed if isinstance(parsed, dict) else None), (
        usage if isinstance(usage, dict) else None
    )


async def summarise_request(
    request: SummaryRequest,
    all_messages: list[dict[str, Any]],
    previous_summary: dict[str, Any] | None = None,
    *,
    model: str,
    api_key: str,
) -> SummaryOutcome:
    """Produce the summary for the range the planner decided to evict.

    Never raises: a failure degrades to the deterministic extractor with
    ``quality="fallback"``, because losing a summary must not lose the answer.
    """
    previous_summary = previous_summary or {}
    evicted = messages_between(
        all_messages,
        request.covered_from_message_id,
        request.covered_until_message_id,
    )
    if len(evicted) != request.message_count:
        return SummaryOutcome(
            delta=SummaryDelta(
                previous_version=request.previous_version,
                quality="fallback",
                reason="evicted_range_not_contiguous",
            ),
            used_model=False,
        )

    material = _material(previous_summary, evicted, MAX_SUMMARY_INPUT_TOKENS)
    fields: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    quality = "fallback"
    reason = "no_model_configured"
    used_model = False
    if api_key.strip():
        fields, usage = await _summarise_with_model(material, model=model, api_key=api_key)
        if fields is not None:
            quality = "model"
            reason = ""
            used_model = True
        else:
            reason = "model_summary_failed"
    if fields is None:
        fields = fallback_fields(evicted)

    normalised = normalise_fields(fields)
    text = truncate_text(render_fields(normalised), MAX_SUMMARY_TOKENS, "tail").text
    if not text.strip():
        quality = "fallback"
        reason = reason or "empty_summary"
    return SummaryOutcome(
        delta=SummaryDelta(
            covered_from_message_id=request.covered_from_message_id,
            covered_until_message_id=request.covered_until_message_id,
            fields=normalised,
            previous_version=request.previous_version,
            text=text,
            quality=quality,
            reason=reason,
        ),
        used_model=used_model,
        usage=usage,
    )


def summarize_sync(
    request: SummaryRequest,
    all_messages: list[dict[str, Any]],
    previous_summary: dict[str, Any] | None = None,
) -> SummaryOutcome:
    """No-await path: the deterministic extractor, marked as such.

    Used when there is no event loop to await on. It is honest about being a
    fallback rather than pretending a model wrote it.
    """
    evicted = messages_between(
        all_messages,
        request.covered_from_message_id,
        request.covered_until_message_id,
    )
    if len(evicted) != request.message_count:
        return SummaryOutcome(
            delta=SummaryDelta(
                previous_version=request.previous_version,
                quality="fallback",
                reason="evicted_range_not_contiguous",
            ),
            used_model=False,
        )
    fields = normalise_fields(fallback_fields(evicted))
    text = truncate_text(render_fields(fields), MAX_SUMMARY_TOKENS, "tail").text
    return SummaryOutcome(
        delta=SummaryDelta(
            covered_from_message_id=request.covered_from_message_id,
            covered_until_message_id=request.covered_until_message_id,
            fields=fields,
            previous_version=request.previous_version,
            text=text,
            quality="fallback",
            reason="sync_path_uses_extractor",
        ),
        used_model=False,
    )
