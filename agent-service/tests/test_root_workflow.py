import asyncio

from app.api.schemas import AgentStatePayload, ChatMessage, ChatRequest, UserContext
from app.graph.root import build_root_graph
from app.graph.runner import run_graph
from langgraph.checkpoint.memory import InMemorySaver


def make_request(
    request_id: str,
    message: str,
    risk_level=None,
    roles=None,
) -> ChatRequest:
    if roles is None:
        roles = ["network_admin"] if risk_level == "high" else []
    return ChatRequest(
        request_id=request_id,
        user_context=UserContext(userId="u1", roles=roles),
        conversation_id="c1",
        user_message_id="m1",
        assistant_message_id="m2",
        messages=[ChatMessage(role="user", content=message)],
        latest_user_message=message,
        agent_state=AgentStatePayload(
            requestId=request_id,
            userContext=UserContext(userId="u1", roles=roles),
            riskLevel=risk_level,
        ),
    )


def test_capability_query_answers_without_model_call():
    events = []

    async def emit(event):
        events.append(event)

    asyncio.run(
        run_graph(
            make_request("cap_test", "你可以做什么事情"),
            emit,
            graph=build_root_graph(InMemorySaver()),
        )
    )
    types = [event.get("type") for event in events]

    assert "answer" in types
    assert "complete" in types
    assert "tool_call" not in types


def test_high_risk_workflow_interrupts_before_write():
    events = []

    async def emit(event):
        events.append(event)

    asyncio.run(
        run_graph(
            make_request("risk_test", "申请VPN权限", risk_level="high"),
            emit,
            graph=build_root_graph(InMemorySaver()),
        )
    )
    types = [event.get("type") for event in events]

    assert "approval_required" in types
    assert "complete" not in types
    assert "vpn_api_provision" not in [
        event.get("name") for event in events if event.get("type") == "tool_call"
    ]


def test_high_risk_workflow_denied_without_required_role():
    events = []

    async def emit(event):
        events.append(event)

    asyncio.run(
        run_graph(
            make_request("risk_denied", "申请VPN权限", risk_level="high", roles=[]),
            emit,
            graph=build_root_graph(InMemorySaver()),
        )
    )
    types = [event.get("type") for event in events]

    assert "error" in types
    assert "approval_required" not in types
    assert "vpn_api_provision" not in [
        event.get("name") for event in events if event.get("type") == "tool_call"
    ]
