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
    # Every declared tool is a real one, and an agent may use only its own.
    assert can_agent_use_tool("network", "ticket_merge") is True
    assert can_agent_use_tool("it", "ticket_merge") is True
    assert can_agent_use_tool("knowledge", "ticket_merge") is False
    assert can_agent_use_tool("approval", "ticket_merge") is False
    # The deleted stubs are declared for nobody.
    for gone in ("vpn_api", "idp", "erp", "hris", "cmdb", "temporal"):
        assert can_agent_use_tool("it", gone) is False
        assert can_agent_use_tool("network", gone) is False


def test_every_declared_tool_is_in_the_catalog():
    """A declared tool with no implementation would refuse at runtime; catch it here instead."""
    from app.tools.catalog import TOOLS

    for agent, spec in AGENTS.items():
        for tool in spec.get("tools", []):
            assert tool in TOOLS, f"{agent} declares {tool}, which is not in the catalog"

