import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.context.builder import assemble_context
from app.gateway.litellm import stream_completion
from app.registry.registry import get_agent, list_agents


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

    agent = get_agent(str(state.get("selected_agent", "knowledge")))
    assembled = assemble_context(state, agent, list_agents())
    system = assembled.system
    messages = assembled.messages
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    "ContextAssembler 已完成预算分配，"
                    f"context_hash={assembled.context_hash[:12]}。"
                ),
                "step": True,
            }
        )

    answer_parts: list[str] = []
    try:
        async with asyncio.timeout(settings.llm_timeout_seconds):
            async for event_type, chunk in stream_completion(
                messages=messages,
                model=state.get("model"),
                api_key=state.get("api_key"),
                system=system,
                temperature=0.3 if state.get("risk_level") == "high" else 0.6,
            ):
                if emit:
                    if event_type == "reasoning":
                        await emit({"type": "thinking", "content": chunk})
                    else:
                        answer_parts.append(chunk)
                        await emit({"type": "answer", "content": chunk})
    except TimeoutError as exc:
        raise RuntimeError("模型生成超时，已终止本次请求") from exc

    return {
        "final_answer": "".join(answer_parts),
        "context_report": assembled.report,
        "context_hash": assembled.context_hash,
    }


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
