from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.graph.nodes.generate import generate_answer
from app.graph.nodes.guardian import guardian_input, guardian_output
from app.graph.nodes.knowledge import retrieve_knowledge
from app.graph.nodes.orchestrator import orchestrate
from app.graph.nodes.service_workflow import (
    approval_gate,
    collect_slots,
    execute_approved_action,
    prepare_approval,
    prepare_read_only_tools,
    validate_permissions,
    verify_result,
)
from app.graph.nodes.tools import execute_tools
from app.graph.state import AgentState


def route_after_guardian(state: AgentState) -> str:
    return "blocked" if state.get("status") == "failed" else "ok"


def route_domain(state: AgentState) -> str:
    if state.get("risk_level") == "high":
        return "high_risk_service"
    if state.get("route") == "knowledge":
        return "knowledge"
    return "service"


def route_after_approval(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "generate_answer"
    return "execute_approved_action"


def route_after_permission_validation(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "generate_answer"
    return "prepare_read_only_tools"


def build_knowledge_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("retrieve_knowledge", retrieve_knowledge)
    workflow.add_node("generate_answer", generate_answer)
    workflow.set_entry_point("retrieve_knowledge")
    workflow.add_edge("retrieve_knowledge", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def build_service_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("execute_tools", execute_tools)
    workflow.add_node("generate_answer", generate_answer)
    workflow.set_entry_point("execute_tools")
    workflow.add_edge("execute_tools", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def build_high_risk_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("collect_slots", collect_slots)
    workflow.add_node("validate_permissions", validate_permissions)
    workflow.add_node("prepare_read_only_tools", prepare_read_only_tools)
    workflow.add_node("prepare_approval", prepare_approval)
    workflow.add_node("approval_gate", approval_gate)
    workflow.add_node("execute_approved_action", execute_approved_action)
    workflow.add_node("verify_result", verify_result)
    workflow.add_node("generate_answer", generate_answer)

    workflow.set_entry_point("collect_slots")
    workflow.add_edge("collect_slots", "validate_permissions")
    workflow.add_conditional_edges(
        "validate_permissions",
        route_after_permission_validation,
        {
            "generate_answer": "generate_answer",
            "prepare_read_only_tools": "prepare_read_only_tools",
        },
    )
    workflow.add_edge("prepare_read_only_tools", "prepare_approval")
    workflow.add_edge("prepare_approval", "approval_gate")
    workflow.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            "generate_answer": "generate_answer",
            "execute_approved_action": "execute_approved_action",
        },
    )
    workflow.add_edge("execute_approved_action", "verify_result")
    workflow.add_edge("verify_result", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def build_root_graph(checkpointer=None):
    workflow = StateGraph(AgentState)
    workflow.add_node("guardian_input", guardian_input)
    workflow.add_node("orchestrate", orchestrate)
    workflow.add_node("knowledge", build_knowledge_workflow())
    workflow.add_node("service", build_service_workflow())
    workflow.add_node("high_risk_service", build_high_risk_workflow())
    workflow.add_node("guardian_output", guardian_output)

    workflow.set_entry_point("guardian_input")
    workflow.add_conditional_edges(
        "guardian_input",
        route_after_guardian,
        {
            "ok": "orchestrate",
            "blocked": "guardian_output",
        },
    )
    workflow.add_conditional_edges(
        "orchestrate",
        route_domain,
        {
            "knowledge": "knowledge",
            "service": "service",
            "high_risk_service": "high_risk_service",
        },
    )
    workflow.add_edge("knowledge", "guardian_output")
    workflow.add_edge("service", "guardian_output")
    workflow.add_edge("high_risk_service", "guardian_output")
    workflow.add_edge("guardian_output", END)

    return workflow.compile(checkpointer=checkpointer or InMemorySaver())
