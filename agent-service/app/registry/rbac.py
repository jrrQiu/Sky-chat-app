from typing import Any

from app.registry.registry import get_agent


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "employee": {
        "knowledge:read",
    },
    "it_user": {
        "it:password_reset",
        "it:device:read",
    },
    "it_staff": {
        "it:password_reset",
        "it:device:read",
        "it:software:install",
    },
    "network_user": {
        "network:vpn:read",
        "network:access:read",
    },
    "network_admin": {
        "network:vpn:read",
        "network:vpn:create",
        "network:access:read",
    },
    "hr_user": {
        "hr:leave:read",
        "hr:onboarding:read",
    },
    "hr_staff": {
        "hr:profile:read",
        "hr:leave:read",
        "hr:onboarding:read",
    },
    "finance_user": {
        "finance:invoice:read",
        "finance:budget:read",
    },
    "approver": {
        "approval:read",
        "approval:create",
    },
    "auditor": {
        "knowledge:read",
        "audit:write",
        "policy:enforce",
    },
}


TOOL_PERMISSIONS: dict[str, set[str]] = {
    "agent_registry": {"agent:select"},
    "policy_api": {"run:read"},
    "idp": {"it:password_reset"},
    "mdm": {"it:device:read"},
    "software_center": {"it:software:install"},
    "vpn_api": {"network:vpn:create"},
    "vpn_api_provision": {"network:vpn:create"},
    "cmdb": {"network:access:read"},
    "permission_system": {"network:access:read"},
    "hris": {"hr:profile:read", "hr:leave:read"},
    "organization_service": {"hr:profile:read"},
    "form_service": {"hr:onboarding:read"},
    "erp": {"finance:invoice:read"},
    "ocr": {"finance:invoice:read"},
    "budget_service": {"finance:budget:read"},
    "pgvector": {"knowledge:read"},
    "elasticsearch": {"knowledge:read"},
    "reranker": {"knowledge:read"},
    "rule_engine": {"approval:read"},
    "temporal": {"approval:create"},
    "notification_system": {"approval:read"},
    "approval_decision": {"approval:create"},
    "finance_submit": {"finance:invoice:read"},
    "rbac": {"policy:enforce"},
    "dlp": {"policy:enforce"},
    "audit_service": {"audit:write"},
}


def all_permissions() -> set[str]:
    return {permission for permissions in ROLE_PERMISSIONS.values() for permission in permissions}


def granted_permissions(user_context: dict[str, Any] | None) -> set[str]:
    roles = set((user_context or {}).get("roles", []))
    if "admin" in roles:
        return all_permissions()

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
        required = set(get_agent(agent_id).get("permissions", []))
    return has_any_permission(user_context, required)
