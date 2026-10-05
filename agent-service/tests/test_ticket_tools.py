"""The tool catalog and the ticket adapter.

Two things are pinned here:

* the catalog is the **single** description of a tool -- its permission, the schema the model
  sees, and the argument allow-list the guardrail enforces all come from one entry, because they
  drifted apart and `ticket_merge` shipped with arguments its own safety rail rejected;
* the ticket adapter talks to Java for real, and when it cannot it reports failure rather than
  inventing a result.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.core.guardrails import allowed_arg_keys, screen_tool_input
from app.core.internal_token import CallerTokenUnavailable, mint_java_caller_token
from app.registry.rbac import TOOL_PERMISSIONS, authorize_tool
from app.tools.adapters import TICKET_TOOLS, tool_status
from app.tools.adapters.base import RefusingToolAdapter, RoutingToolAdapter
from app.tools.adapters.ticket import (
    CALLER_TOKEN_UNAVAILABLE,
    MERGE_TARGETS_MISSING,
    TICKET_SERVICE_UNAVAILABLE,
    MergeTargetsMissing,
    TicketToolAdapter,
    infer_impact,
    infer_ticket_type,
    infer_urgency,
)
from app.tools.catalog import (
    TOOLS,
    approval_gated_tools,
    arg_keys,
    catalog_status,
    direct_tools,
    openai_tools,
    permission_for,
)
from app.tools.selection import select_tools_for_request, ticket_tools_for_message

SECRET = "unit-test-internal-secret"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, raw: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._raw = raw

    def json(self):
        if self._raw is not None:
            raise json.JSONDecodeError("boom", self._raw, 0)
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient so the real request-building code runs."""

    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        if self.error is not None:
            raise self.error
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def adapter_with(client: FakeClient) -> TicketToolAdapter:
    return TicketToolAdapter(client_factory=lambda: client, base_url="http://java.test:8080")


def state(user_id: str = "user-1", roles: list[str] | None = None, **extra):
    return {
        "user_id": user_id,
        "user_context": {"roles": roles or ["employee"]},
        "latest_user_message": "打印机坏了",
        "conversation_id": "conv-9",
        **extra,
    }


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "java_service_base_url", "http://java.test:8080")
    yield


# --------------------------------------------------------------------------- catalog


def test_the_catalog_contains_only_real_tools():
    # The IdP/MDM/HRIS/ERP stubs are gone; a model must not be offered a tool that cannot act.
    assert set(TOOLS) == {
        "ticket_create",
        "ticket_list",
        "ticket_get",
        "ticket_comment",
        "ticket_merge",
    }
    for gone in ("idp", "mdm", "software_center", "erp", "ocr", "hris", "vpn_api", "temporal"):
        assert gone not in TOOLS


def test_one_entry_drives_permission_schema_and_the_guardrail():
    for name, spec in TOOLS.items():
        # The permission comes from the catalog...
        assert TOOL_PERMISSIONS[name] == {spec.permission}
        assert permission_for(name) == spec.permission
        # ...the model's schema comes from the catalog...
        schema = openai_tools([name])[0]["function"]
        assert set(schema["parameters"]["properties"]) == set(spec.parameters)
        assert schema["parameters"]["additionalProperties"] is False
        # ...and so does the guardrail allow-list.
        assert arg_keys(name) <= allowed_arg_keys(name)


def test_the_guardrail_rejects_arguments_outside_the_declared_schema():
    # The model may not invent arguments, and this is the rail that says so.
    verdict = screen_tool_input("ticket_create", {"title": "x", "assigneeId": "someone"})
    assert verdict.allowed is False
    assert verdict.reason == "TOOL_ARGUMENT_NOT_ALLOWED"
    assert screen_tool_input("ticket_create", {"title": "x", "impact": "high"}).allowed is True


def test_the_approval_gated_tool_is_never_offered_to_the_model():
    assert approval_gated_tools() == ["ticket_merge"]
    assert "ticket_merge" not in direct_tools()
    assert [t["function"]["name"] for t in openai_tools(direct_tools())] == sorted(
        direct_tools(), key=direct_tools().index
    )


def test_only_staff_roles_may_merge():
    assert authorize_tool({"roles": ["it_staff"]}, "it", "ticket_merge") is True
    assert authorize_tool({"roles": ["network_admin"]}, "network", "ticket_merge") is True
    assert authorize_tool({"roles": ["employee"]}, "it", "ticket_merge") is False
    assert authorize_tool({"roles": ["auditor"]}, "it", "ticket_merge") is False


def test_reading_requires_only_ticket_read():
    assert authorize_tool({"roles": ["auditor"]}, "it", "ticket_list") is True
    assert authorize_tool({"roles": ["auditor"]}, "it", "ticket_create") is False


def test_catalog_status_reports_gating_and_permissions():
    status = catalog_status()
    names = {entry["name"] for entry in status["tools"]}
    assert names == set(TOOLS)
    assert status["approvalGated"] == ["ticket_merge"]
    assert "ticket_merge" not in status["modelCallable"]


def test_tool_status_separates_implemented_from_declared():
    status = tool_status()

    assert set(status["implemented"]) == set(TOOLS)
    # Every implemented tool is declared for at least one agent now.
    assert status["implemented_not_wired"] == []
    assert status["declared_unimplemented"] == []
    assert status["adapters"]["ticket"] == "internal-http"
    # There is no mock provider: anything unimplemented refuses.
    assert status["adapters"]["others"] in {"refuse", "openapi"}


def test_an_unimplemented_tool_refuses_rather_than_inventing_a_result():
    import asyncio

    result = asyncio.run(RefusingToolAdapter().execute("idp", {}, {}))
    assert result["success"] is False
    assert result["error"] == "TOOL_NOT_IMPLEMENTED"
    assert "未执行" in result["text"]


# --------------------------------------------------------------------------- token


def test_caller_token_uses_the_reverse_issuer_audience_pair():
    import jwt

    token = mint_java_caller_token("user-7", ["it_staff"])
    claims = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=settings.java_service_audience,
        issuer=settings.agent_caller_issuer,
    )

    assert claims["sub"] == "user-7"
    assert claims["roles"] == ["it_staff"]
    # The mirror of what Java mints. If these ever match, tokens become replayable between the
    # two services.
    assert claims["iss"] == "sky-chat-agent-service"
    assert claims["aud"] == "sky-chat-java-service"
    assert claims["iss"] != settings.agent_internal_jwt_issuer
    assert claims["aud"] != settings.agent_internal_jwt_audience


def test_caller_token_needs_a_secret_and_a_subject(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "")
    with pytest.raises(CallerTokenUnavailable):
        mint_java_caller_token("user-7")

    monkeypatch.setattr(settings, "agent_internal_jwt_secret", SECRET)
    with pytest.raises(ValueError):
        mint_java_caller_token("   ")


# --------------------------------------------------------------------------- adapter


@pytest.mark.asyncio
async def test_ticket_create_posts_the_synthesized_arguments():
    client = FakeClient(FakeResponse(200, {"success": True, "text": "已创建", "data": {}}))

    result = await adapter_with(client).execute(
        "ticket_create", {"latest_user_message": "打印机坏了"}, state()
    )

    assert result["success"] is True
    call = client.calls[0]
    assert call["url"] == "http://java.test:8080/v1/internal/tools/ticket_create"
    assert call["headers"]["Authorization"].startswith("Bearer ")
    args = call["json"]["args"]
    assert args["title"] == "打印机坏了"
    assert args["type"] == "incident"
    assert args["conversationId"] == "conv-9"
    # Never chosen here: the impact/urgency matrix owns priority.
    assert "priority" not in args


@pytest.mark.asyncio
async def test_an_unreachable_java_service_is_a_failure_never_a_fabricated_success():
    client = FakeClient(error=httpx.ConnectError("refused"))

    result = await adapter_with(client).execute("ticket_create", {}, state())

    assert result["success"] is False
    assert result["error"] == TICKET_SERVICE_UNAVAILABLE
    assert "未执行" in result["text"]


@pytest.mark.asyncio
async def test_a_rejected_caller_identity_is_reported_as_a_configuration_problem():
    client = FakeClient(FakeResponse(401, {"error": "INVALID_INTERNAL_CALLER"}))

    result = await adapter_with(client).execute("ticket_list", {}, state())

    assert result["success"] is False
    assert result["error"] == "INVALID_INTERNAL_CALLER"
    assert "密钥" in result["text"]


@pytest.mark.asyncio
async def test_a_missing_secret_refuses_instead_of_calling(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "")
    client = FakeClient(FakeResponse(200, {"success": True}))

    result = await adapter_with(client).execute("ticket_create", {}, state())

    assert result["success"] is False
    assert result["error"] == CALLER_TOKEN_UNAVAILABLE
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_unparsable_or_failed_response_is_a_failure():
    assert (await adapter_with(FakeClient(FakeResponse(200, raw="not json")))
            .execute("ticket_list", {}, state()))["success"] is False
    assert (await adapter_with(FakeClient(FakeResponse(500, {"error": "X"})))
            .execute("ticket_list", {}, state()))["success"] is False


@pytest.mark.asyncio
async def test_the_200_body_is_passed_through():
    body = {"success": True, "text": "共 1 张工单：\n#12 [new] 打印机", "data": {"total": 1}}
    result = await adapter_with(FakeClient(FakeResponse(200, body))).execute(
        "ticket_list", {"limit": 5}, state()
    )

    assert result["text"] == body["text"]
    assert result["data"] == {"total": 1}


@pytest.mark.asyncio
async def test_merge_reads_the_two_numbers_in_the_order_mentioned():
    client = FakeClient(FakeResponse(200, {"success": True, "text": "ok", "data": {}}))

    await adapter_with(client).execute(
        "ticket_merge", {"latest_user_message": "帮我把 #12 合并到 #14"}, state()
    )

    # "把 A 合并到 B" means source A, target B -- the two are not interchangeable.
    assert client.calls[0]["json"]["args"]["sourceNumber"] == 12
    assert client.calls[0]["json"]["args"]["targetNumber"] == 14


def test_a_merge_without_two_numbers_is_refused_with_a_usable_message():
    adapter = TicketToolAdapter(base_url="http://java.test:8080")

    with pytest.raises(MergeTargetsMissing) as raised:
        adapter._tool_args("ticket_merge", {"latest_user_message": "帮我合并工单"}, {})

    assert "未执行合并" in str(raised.value)


@pytest.mark.asyncio
async def test_a_merge_refusal_becomes_a_result_rather_than_an_exception(monkeypatch):
    class ExplodingClient:
        async def post(self, *args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("no call should be made without two tickets")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(settings, "agent_internal_jwt_secret", SECRET)
    adapter = TicketToolAdapter(
        client_factory=lambda: ExplodingClient(), base_url="http://java.test:8080"
    )

    result = await adapter.execute(
        "ticket_merge",
        {"latest_user_message": "帮我合并工单"},
        {"user_id": "u1", "user_context": {"roles": ["it_staff"]}},
    )

    assert result["success"] is False
    assert result["error"] == MERGE_TARGETS_MISSING
    assert "未执行" in result["text"]


# --------------------------------------------------------------------------- arguments


@pytest.mark.parametrize(
    "message,expected",
    [
        ("打印机坏了", "incident"),
        ("无法登录 VPN", "incident"),
        ("申请一个显示器", "request"),
        ("需要开通财务系统权限", "request"),
        # A message that is both is filed as the fault: it is the actionable half.
        ("打印机坏了，顺便申请个新墨盒", "incident"),
    ],
)
def test_ticket_type_inference(message, expected):
    assert infer_ticket_type(message) == expected


def test_impact_and_urgency_heuristics():
    assert infer_impact("服务器宕机了") == "high"
    assert infer_impact("我的鼠标有点问题") == "medium"
    assert infer_urgency("很急，尽快处理") == "high"
    assert infer_urgency("有空的时候看看") == "medium"


# --------------------------------------------------------------------------- routing


@pytest.mark.asyncio
async def test_routing_sends_tickets_to_the_real_adapter_and_refuses_the_rest():
    client = FakeClient(FakeResponse(200, {"success": True, "text": "ok", "data": {}}))
    router = RoutingToolAdapter(ticket_adapter=adapter_with(client))

    real = await router.execute("ticket_list", {}, state())
    assert client.calls, "the ticket tool must reach the HTTP adapter"
    assert "simulated" not in real

    # There is no fallback that fabricates: an unimplemented tool refuses.
    refused = await router.execute("idp", {}, state())
    assert refused["success"] is False
    assert refused["error"] == "TOOL_NOT_IMPLEMENTED"


def test_ticket_tools_are_not_selectable_as_write_actions():
    # Rule-based selection must never pick the approval-gated tool.
    for message in ("合并工单", "帮我把 #12 合并到 #14", "我重复提交了两张工单"):
        assert "ticket_merge" not in select_tools_for_request("it", message)


# --------------------------------------------------------------------------- selection


def test_a_fault_raises_a_ticket():
    assert "ticket_create" in select_tools_for_request("it", "我的打印机坏了")


def test_a_status_question_lists_tickets_instead_of_creating_one():
    selected = select_tools_for_request("it", "我的工单处理到哪了？")
    assert "ticket_list" in selected
    assert "ticket_create" not in selected


def test_unrelated_messages_do_not_touch_tickets():
    assert select_tools_for_request("it", "今天天气怎么样") == []


def test_capabilities_action_never_selects_tools():
    assert select_tools_for_request("it", "打印机坏了", action="capabilities") == []


def test_auto_creation_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ticket_auto_create_from_chat", False)
    assert ticket_tools_for_message("打印机坏了", list(TICKET_TOOLS)) == []
    # Listing still works: the switch only governs automatic creation.
    assert "ticket_list" in ticket_tools_for_message("我的工单", list(TICKET_TOOLS))
