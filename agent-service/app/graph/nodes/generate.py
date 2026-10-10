import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.context.builder import (
    BudgetInfeasible,
    assemble_context,
    summarize_context,
)
from app.gateway.litellm import stream_completion
from app.observability.calibration import (
    calibration,
    record_budget_infeasible,
    record_context_report,
)
from app.registry.registry import get_agent, list_agents

logger = logging.getLogger(__name__)


async def generate_answer(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    if state.get("status") == "failed":
        message = "请求已终止：" + str(state.get("guard_error") or "安全校验未通过")
        emit = config.get("configurable", {}).get("emit") if config else None
        if emit:
            await emit({"type": "answer", "content": message})
        return {"final_answer": message}

    emit = config.get("configurable", {}).get("emit") if config else None
    if state.get("action") == "capabilities":
        answer = capabilities_answer()
        if emit:
            await emit({"type": "thinking", "content": "已生成平台能力清单。", "step": True})
            await emit({"type": "answer", "content": answer})
        return {"final_answer": answer}

    if state.get("action") == "clarify":
        answer = state.get("clarifying_question") or "请补充关键信息后重试。"
        if emit:
            await emit({"type": "thinking", "content": "意图置信度不足，正在请求澄清。", "step": True})
            await emit({"type": "answer", "content": answer})
        return {"final_answer": answer}

    if state.get("clarification_required"):
        # A tool failed deterministically in a way the user can fix, so asking is the whole
        # turn: the model would only rephrase the same question, and no amount of generation
        # can supply a ticket number the user has not given. The reply arrives as the next
        # message and the agent retries the tool with it -- that is the slot-filling loop.
        answer = state.get("clarifying_question") or "请补充关键信息后重试。"
        if emit:
            await emit(
                {
                    "type": "thinking",
                    "content": "工具调用未能完成，需要用户补充信息后重试。",
                    "step": True,
                }
            )
            # Structured as well as prose, so a client that can render a form does not have to
            # parse the sentence. Clients that do not know the type ignore it.
            await emit(
                {
                    "type": "clarification_required",
                    "question": answer,
                    "missingFields": list(state.get("missing_fields") or []),
                }
            )
            await emit({"type": "answer", "content": answer})
        return {"final_answer": answer}

    agent = get_agent(str(state.get("selected_agent", "knowledge")))
    try:
        assembled = assemble_context(state, agent, list_agents())
    except BudgetInfeasible as error:
        # A budget that cannot satisfy its guarantees is refused *before* the model
        # call. The alternative the old engine chose -- send a degraded prompt and
        # note it in a report nobody reads -- answers with a half policy statement.
        window, _max_output = settings.effective_context_limits(
            str(state.get("model") or settings.deepseek_model)
        )
        record_budget_infeasible(window=window, missing_tokens=error.missing_tokens)
        message = (
            "本次请求的上下文无法在模型窗口内完整容纳（"
            f"缺少 {error.missing_tokens} tokens）：{error.detail}。"
            "请缩短输入或联系管理员调整模型窗口配置。"
        )
        if emit:
            await emit({"type": "error", "message": message})
        return {"final_answer": message, "status": "failed", "guard_error": "BUDGET_INFEASIBLE"}

    # The summary is produced *before* the answer so it is part of this prompt;
    # the planner decided which range it must cover.
    if assembled.needs_summary:
        try:
            await summarize_context(assembled, state)
        except Exception:
            # A summary failure must not lose the answer; the report already
            # records which messages were evicted.
            logger.warning(
                "Rolling summary failed; continuing with the eviction report only",
                exc_info=True,
            )

    system = assembled.system
    messages = assembled.messages
    model_name = str(state.get("model") or settings.deepseek_model)
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    "ContextEngine v2 已完成预算分配，"
                    f"used/usable={assembled.report.get('used_tokens')}/"
                    f"{assembled.report.get('usable_tokens')}，"
                    f"context_hash={assembled.context_hash[:12]}。"
                ),
                "step": True,
            }
        )

    answer_parts: list[str] = []
    usage: dict[str, Any] | None = None
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds):
            async for event_type, chunk in stream_completion(
                messages=messages,
                model=state.get("model"),
                api_key=state.get("api_key"),
                system=system,
                temperature=0.3 if state.get("risk_level") == "high" else 0.6,
                max_tokens=assembled.report.get("max_output"),
            ):
                if event_type == "usage":
                    # The provider's own count. Not emitted to the user: it is
                    # measurement, not answer text.
                    usage = chunk if isinstance(chunk, dict) else None
                    continue
                if emit:
                    if event_type == "reasoning":
                        await emit({"type": "thinking", "content": chunk})
                    else:
                        answer_parts.append(str(chunk))
                        await emit({"type": "answer", "content": chunk})
                elif event_type == "answer":
                    answer_parts.append(str(chunk))
    except TimeoutError as exc:
        raise RuntimeError("模型生成超时，已终止本次请求") from exc

    _finalise_report(assembled, usage, model_name)
    if emit:
        # Sent to the server (Java) for persistence and to the client for
        # diagnostics. Clients that do not know the type ignore it.
        await emit({"type": "context_report", "report": assembled.report})

    return {
        "final_answer": "".join(answer_parts),
        "context_report": assembled.report,
        "context_hash": assembled.context_hash,
    }


def _finalise_report(
    assembled,
    usage: dict[str, Any] | None,
    model_name: str,
) -> None:
    """Fold the provider's measurement into the report and the calibration."""
    if usage:
        assembled.report["provider_usage"] = dict(usage)
    from app.context.report import estimate_error_ratio

    estimated = assembled.report.get("estimated_input_tokens")
    provider_input = None
    if isinstance(usage, dict):
        raw = usage.get("prompt_tokens", usage.get("input_tokens"))
        try:
            provider_input = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            provider_input = None
    ratio = estimate_error_ratio(estimated, provider_input)
    if ratio is not None:
        assembled.report["estimate_error_ratio"] = ratio
    calibration.record(
        model_name,
        estimated_input_tokens=estimated,
        provider_usage=usage,
    )
    record_context_report(assembled.report, model=model_name)


def capabilities_answer() -> str:
    examples = {
        "it": "密码重置、账号解锁、软件安装、设备故障处理",
        "network": "VPN、Wi-Fi、防火墙、内网访问申请与查询",
        "hr": "请假、入职、在职证明、员工信息查询",
        "finance": "报销、发票、预算、付款进度查询",
        "knowledge": "制度、SOP、历史工单和常见问题检索",
        "approval": "高风险操作审批、审批状态与规则查询",
        "guardian": "安全校验、敏感数据防护、权限复核",
    }
    lines = ["我可以处理以下企业服务台任务：", ""]
    for agent in list_agents():
        if agent["id"] in {"orchestrator", "guardian"}:
            continue
        lines.append(
            f"- {agent['name']}："
            f"{examples.get(agent['id'], agent['description'])}"
        )
    lines.append("")
    lines.append("你可以直接描述问题，例如“查询剩余年假”或“申请 VPN 权限”。")
    return "\n".join(lines)
