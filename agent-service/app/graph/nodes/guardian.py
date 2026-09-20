import re
from typing import Any

from langchain_core.runnables import RunnableConfig

PROMPT_INJECTION = re.compile(
    r"(ignore previous|ignore all|忽略以上|忽略之前|系统提示|override system|bypass policy)",
    re.I,
)
SENSITIVE_CONTENT = re.compile(
    r"(\b\d{15,19}\b|\b\d{3}-\d{2}-\d{4}\b|password\s*[:=]|token\s*[:=]|身份证号|银行卡号)"
)


def _emit(config: RunnableConfig | None):
    if not config:
        return None
    return config.get("configurable", {}).get("emit")


def redact_text(text: str) -> str:
    return SENSITIVE_CONTENT.sub("[REDACTED]", text)


def guard_input(text: str) -> dict[str, Any]:
    if PROMPT_INJECTION.search(text):
        return {"allowed": False, "reason": "Detected prompt injection attempt"}

    if SENSITIVE_CONTENT.search(text):
        return {"allowed": False, "reason": "Detected sensitive data in user input"}

    return {"allowed": True}


async def guardian_input(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    result = guard_input(str(state.get("latest_user_message", "")))
    emit = _emit(config)

    if not result["allowed"]:
        if emit:
            await emit(
                {
                    "type": "error",
                    "message": result["reason"],
                }
            )
        return {
            "status": "failed",
            "guard_error": result["reason"],
            "final_answer": f"Guardian 已拦截请求：{result['reason']}",
        }

    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "Guardian 输入检查通过。",
                "step": True,
            }
        )

    return {"status": "processing", "guard_error": None}


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
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "Guardian 已完成权限、敏感数据和结果复核。",
                "step": True,
            }
        )

    return {"status": "completed", "final_answer": final_answer}
