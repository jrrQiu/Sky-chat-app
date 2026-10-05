"""Authorization: agent access, and per-tool permissions derived from the catalog.

The model changed when the stub tools were removed. Before, each domain had its own read/write
permissions (`network:access:read`, `it:software:install`, ...) and *those* gated access to the
agents. With the stubs gone, gating "may you talk to the IT agent" behind a permission that
existed only to gate a deleted tool made no sense, so agent access is now `service:request`,
held by every authenticated role, and **everything that changes state is gated per tool**.

That is a deliberate posture: anyone may report a problem or ask a question; nobody may merge
tickets without `ticket:merge`.
"""

from app.registry.rbac import (
    ROLE_PERMISSIONS,
    TOOL_PERMISSIONS,
    authorize_agent,
    authorize_tool,
    granted_permissions,
    has_any_permission,
)
from app.tools.catalog import TOOLS


def test_role_expands_to_permissions():
    permissions = granted_permissions({"roles": ["network_user"]})
    assert "service:request" in permissions
    assert "ticket:create" in permissions
    # Reading and creating do not imply writing destructively.
    assert "ticket:merge" not in permissions


def test_tool_permissions_come_from_the_catalog():
    for name, spec in TOOLS.items():
        assert TOOL_PERMISSIONS[name] == {spec.permission}


def test_any_authenticated_role_may_reach_a_service_agent():
    # Reporting a problem is not a privilege; the write is what gets gated.
    for role in ("employee", "it_user", "it_staff", "network_user", "hr_user", "finance_user"):
        assert authorize_agent({"roles": [role]}, "it") is True, role
        assert authorize_agent({"roles": [role]}, "network") is True, role


def test_read_only_oversight_cannot_reach_the_service_agents():
    # `auditor` holds only knowledge:read and ticket:read: it may look, not ask the agent to act.
    assert authorize_agent({"roles": ["auditor"]}, "knowledge") is True
    assert authorize_agent({"roles": ["auditor"]}, "it") is False


def test_tool_rbac_distinguishes_read_from_destructive_write():
    reader = {"roles": ["network_user"]}
    assert authorize_tool(reader, "network", "ticket_list") is True
    assert authorize_tool(reader, "network", "ticket_create") is True
    # The destructive one needs its own permission.
    assert authorize_tool(reader, "network", "ticket_merge") is False
    assert authorize_tool({"roles": ["network_admin"]}, "network", "ticket_merge") is True


def test_the_auditor_may_read_tickets_but_not_write_them():
    auditor = {"roles": ["auditor"]}
    assert authorize_tool(auditor, "it", "ticket_list") is True
    assert authorize_tool(auditor, "it", "ticket_create") is False
    assert authorize_tool(auditor, "it", "ticket_comment") is False
    assert authorize_tool(auditor, "it", "ticket_merge") is False


def test_admin_holds_every_permission():
    admin = {"roles": ["admin"]}
    for name in TOOLS:
        assert authorize_tool(admin, "it", name) is True, name
    for role_permissions in ROLE_PERMISSIONS.values():
        assert role_permissions <= granted_permissions(admin)


def test_unknown_tool_falls_back_to_the_agent_gate_not_to_allow():
    # An uncatalogued tool must not be treated as "no permission required".
    assert authorize_tool({"roles": ["employee"]}, "it", "made_up_tool") is True  # employee has service:request
    assert authorize_tool({"roles": ["auditor"]}, "it", "made_up_tool") is False


def test_empty_roles_keep_public_knowledge_readable():
    assert has_any_permission(None, {"knowledge:read"}) is True
    assert has_any_permission(None, {"ticket:merge"}) is False
