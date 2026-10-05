from typing import Any

from app.registry.registry import get_agent


#: What each role may do.
#:
#: `service:request` gates *access to the service-desk agents* (it replaced the per-domain
#: permissions that only existed to gate the deleted stub tools). Everything that changes state
#: is still gated individually below, so being able to talk to the IT agent does not mean being
#: able to merge tickets.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "employee": {
        "knowledge:read",
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "it_user": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "it_staff": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
        "ticket:merge",
    },
    "network_user": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "network_admin": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
        "ticket:merge",
    },
    "hr_user": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "hr_staff": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
        "ticket:merge",
    },
    "finance_user": {
        "service:request",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "approver": {
        "service:request",
        "approval:read",
        "approval:create",
        "ticket:read",
        "ticket:create",
        "ticket:comment",
    },
    "auditor": {
        # Read-only oversight: may look, may not raise or reply through the agent.
        "knowledge:read",
        "ticket:read",
    },
}


def _tool_permissions() -> dict[str, set[str]]:
    """Derived from the catalog, so a tool and its permission cannot drift apart."""
    from app.tools.catalog import TOOLS

    return {
        name: {spec.permission}
        for name, spec in TOOLS.items()
        if spec.permission
    }


TOOL_PERMISSIONS: dict[str, set[str]] = _tool_permissions()


def all_permissions() -> set[str]:
    return {permission for permissions in ROLE_PERMISSIONS.values() for permission in permissions}


def granted_permissions(user_context: dict[str, Any] | None) -> set[str]:
    roles = set((user_context or {}).get("roles", []))
    if "admin" in roles:
        tool_permissions = {p for perms in TOOL_PERMISSIONS.values() for p in perms}
        return all_permissions() | tool_permissions

    if not roles:
        return {"knowledge:read"}

    permissions: set[str] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions


def has_any_permission(
    user_context: dict[str, Any] | None,
    required: set[str],
) -> bool:
    if not required:
        return True
    return bool(granted_permissions(user_context).intersection(required))


def authorize_agent(
    user_context: dict[str, Any] | None,
    agent_id: str,
) -> bool:
    required = set(get_agent(agent_id).get("permissions", []))
    return has_any_permission(user_context, required)


def authorize_tool(
    user_context: dict[str, Any] | None,
    agent_id: str,
    tool_name: str,
) -> bool:
    required = TOOL_PERMISSIONS.get(tool_name)
    if required is None:
        # Not a catalogued tool: fall back to the agent-level gate rather than allowing it.
        required = set(get_agent(agent_id).get("permissions", []))
    return has_any_permission(user_context, required)
