from app.registry.rbac import (
    authorize_agent,
    authorize_tool,
    granted_permissions,
    has_any_permission,
)


def test_role_expands_to_permissions():
    permissions = granted_permissions({"roles": ["network_user"]})
    assert "network:access:read" in permissions
    assert "network:vpn:create" not in permissions


def test_agent_rbac_requires_runtime_permission():
    assert authorize_agent({"roles": ["network_user"]}, "network") is True
    assert authorize_agent({"roles": ["hr_user"]}, "network") is False


def test_tool_rbac_distinguishes_read_and_write():
    user = {"roles": ["network_user"]}
    assert authorize_tool(user, "network", "cmdb") is True
    assert authorize_tool(user, "network", "vpn_api") is False
    assert authorize_tool({"roles": ["network_admin"]}, "network", "vpn_api") is True


def test_empty_roles_keep_public_knowledge_readable():
    assert has_any_permission(None, {"knowledge:read"}) is True
    assert has_any_permission(None, {"network:vpn:create"}) is False
