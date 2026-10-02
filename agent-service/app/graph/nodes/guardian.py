"""Guardian graph nodes.

The rail primitives live in `app/core/guardrails.py`; this module is only the
graph-integration layer (state updates and streamed events).
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.core.guardrails import guard_input, redact_text

__all__ = [
    "guardian_input",
    "guardian_output",
    "guard_input",
    "redact_text",
]


def _emit(config: RunnableConfig | None):
    if not config:
        return None
    return config.get("configurable", {}).get("emit")


async def guardian_input(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    raw = str(state.get("latest_user_message", ""))
    result = guard_input(raw)

    if not result["allowed"]:
        if emit:
            await emit({"type": "error", "message": result["reason"]})
        return {
            "status": "failed",
            "guard_error": result["reason"],
            "final_answer": f"Guardian 已拦截请求：{result['reason']}",
        }

    update: dict[str, Any] = {"status": "processing", "guard_error": None}
    if result.get("redacted"):
        # Redact what the model sees without rewriting the stored message.
        update["latest_user_message"] = result["sanitized"]

    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    "Guardian 输入检查通过（已脱敏敏感字段）。"
                    if result.get("redacted")
                    else "Guardian 输入检查通过。"
                ),
                "step": True,
            }
        )
    return update


async def guardian_output(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)

    if state.get("status") == "failed":
        if emit:
            await emit(
                {
                    "type": "thinking",
                    "content": "Guardian 复核完成，请求被拒绝。",
                    "step": True,
                }
            )
        return {"status": "failed"}

    final_answer = redact_text(str(state.get("final_answer", "")))
    quarantined = int(state.get("quarantined_evidence_count") or 0)
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    "Guardian 已完成权限、敏感数据和结果复核"
                    + (
                        f"（检索护栏隔离了 {quarantined} 条可疑内容）。"
                        if quarantined
                        else "。"
                    )
                ),
                "step": True,
            }
        )

    return {"status": "completed", "final_answer": final_answer}
