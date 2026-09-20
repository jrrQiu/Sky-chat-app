from app.registry.registry import AGENTS, can_agent_use_tool


def test_registry_contains_v1_agents():
    assert set(AGENTS) == {
        "orchestrator",
        "it",
        "network",
        "hr",
        "finance",
        "knowledge",
        "approval",
        "guardian",
    }


def test_tool_permissions_are_enforced():
    assert can_agent_use_tool("network", "vpn_api") is True
    assert can_agent_use_tool("it", "vpn_api") is False
