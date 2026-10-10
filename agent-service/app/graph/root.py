from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import TimeoutPolicy

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
from app.graph.nodes.tools import confirm_writes, execute_tools
from app.graph.state import AgentState

#: Node-level time budgets (T5). The three windows must be monotonic:
#: one tool < node idle < stream idle, with heartbeats resetting the idle timers so a long
#: but healthy tool never looks like a hung stream.
#:
#: The idle window is read from settings rather than kept as a second constant here: the
#: readiness endpoint validates `settings.node_idle_timeout_seconds`, and a hard-coded copy
#: meant that check reported on a value the graph never used -- an operator could change the
#: setting, watch /ready confirm the ordering, and change nothing at all.
KNOWLEDGE_NODE_RUN_TIMEOUT_SECONDS = 120


def _tool_node_idle_timeout() -> float:
    from app.config import settings

    return float(settings.node_idle_timeout_seconds)


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


def route_after_tools(state: AgentState) -> str:
    """After the loop: pause for confirmation when a `confirm`-tier write was requested."""
    if state.get("pending_confirm_writes"):
        return "confirm_writes"
    return "generate_answer"


def build_knowledge_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node(
        "retrieve_knowledge",
        retrieve_knowledge,
        timeout=TimeoutPolicy(run_timeout=KNOWLEDGE_NODE_RUN_TIMEOUT_SECONDS),
    )
    workflow.add_node(
        "generate_answer",
        generate_answer,
        timeout=TimeoutPolicy(run_timeout=_answer_run_timeout()),
    )
    workflow.set_entry_point("retrieve_knowledge")
    workflow.add_edge("retrieve_knowledge", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def build_service_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    # `execute_tools` gets a timeout but deliberately **no** retry policy: LangGraph retries
    # by re-running the node, which for this node means asking the model to choose tools
    # again (a second model call, at cost) and re-emitting the round's events. The effect
    # ledger makes the write itself safe, but the duplicate model call and the duplicated
    # event stream are not worth it -- see the design's §4.2 note, and the decision recorded
    # in the gap analysis.
    workflow.add_node(
        "execute_tools",
        execute_tools,
        timeout=TimeoutPolicy(
            run_timeout=_tool_node_run_timeout(),
            idle_timeout=_tool_node_idle_timeout(),
        ),
    )
    workflow.add_node("confirm_writes", confirm_writes)
    workflow.add_node(
        "generate_answer",
        generate_answer,
        timeout=TimeoutPolicy(run_timeout=_answer_run_timeout()),
    )
    workflow.set_entry_point("execute_tools")
    workflow.add_conditional_edges(
        "execute_tools",
        route_after_tools,
        {
            "confirm_writes": "confirm_writes",
            "generate_answer": "generate_answer",
        },
    )
    workflow.add_edge("confirm_writes", "generate_answer")
    workflow.add_edge("generate_answer", END)
    return workflow.compile()


def _tool_node_run_timeout() -> float:
    """A ceiling that covers the worst legitimate round count, plus slack.

    Derived from the caps the loop actually enforces, *including* what one tool call may spend
    on retries: ``rounds x (model + min(per-call deadline, attempts x tool timeout + waits))``.
    The first version omitted the retry factor, so a round that legitimately retried twice was
    cut off by this ceiling instead of finishing -- the guard had turned into a functional
    limit that cost the turn its partial results.
    """
    from app.config import settings

    retry_worst = (
        settings.tool_retry_read_attempts * settings.tool_timeout_seconds
        + settings.tool_retry_total_budget_seconds
    )
    per_tool = min(float(settings.tool_call_deadline_seconds), retry_worst)
    per_round = settings.llm_timeout_seconds + per_tool
    return max(60.0, settings.agent_max_rounds * per_round + 30.0)


def _answer_run_timeout() -> float:
    """One model call's worth of wall clock, plus the summary call it may make."""
    from app.config import settings

    return settings.llm_timeout_seconds + 30.0


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
