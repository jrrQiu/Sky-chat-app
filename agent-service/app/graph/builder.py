from langgraph.graph import END, StateGraph

from app.graph.nodes.approval import create_approval
from app.graph.nodes.generate import generate_answer
from app.graph.nodes.guardian import guardian_input, guardian_output
from app.graph.nodes.knowledge import retrieve_knowledge
from app.graph.nodes.orchestrator import orchestrate
from app.graph.nodes.tools import execute_tools
from app.graph.state import AgentState


def route_after_guardian(state: AgentState) -> str:
    return "blocked" if state.get("status") == "failed" else "ok"


def build_agent_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("guardian_input", guardian_input)
    workflow.add_node("orchestrate", orchestrate)
    workflow.add_node("retrieve_knowledge", retrieve_knowledge)
    workflow.add_node("execute_tools", execute_tools)
    workflow.add_node("create_approval", create_approval)
    workflow.add_node("generate_answer", generate_answer)
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
    workflow.add_edge("orchestrate", "retrieve_knowledge")
    workflow.add_edge("retrieve_knowledge", "execute_tools")
    workflow.add_edge("execute_tools", "create_approval")
    workflow.add_edge("create_approval", "generate_answer")
    workflow.add_edge("generate_answer", "guardian_output")
    workflow.add_edge("guardian_output", END)

    return workflow.compile()
