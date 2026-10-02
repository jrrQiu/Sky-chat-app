"""The first real tool: service-desk tickets.

These tests exist to pin the difference between an integration and a stub. The
ticket tools must talk to Java for real (or report failure), while every other tool
must keep advertising that it is simulated.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from app.config import settings
from app.core.internal_token import CallerTokenUnavailable, mint_java_caller_token
from app.tools.adapters import TICKET_TOOLS, tool_status
from app.tools.adapters.base import MockToolAdapter, RoutingToolAdapter
from app.tools.adapters.mock import select_tools_for_request, ticket_tools_for_message
from app.tools.adapters.ticket import (
    CALLER_TOKEN_UNAVAILABLE,
    TICKET_SERVICE_UNAVAILABLE,
    TicketToolAdapter,
    infer_impact,
    infer_ticket_type,
    infer_urgency,
)

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
    # The mirror of what Java mints. If these ever match, tokens become replayable
    # between the two services.
    assert claims["iss"] == "sky-chat-agent-service"
    assert claims["aud"] == "sky-chat-java-service"
    assert claims["iss"] != settings.agent_internal_jwt_issuer
    assert claims["aud"] != settings.agent_internal_jwt_audience
    assert claims["exp"] > dt.datetime.now(dt.timezone.utc).timestamp()


def test_caller_token_needs_a_secret_and_a_subject(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "")
    with pytest.raises(CallerTokenUnavailable):
        mint_java_caller_token("user-7")

    monkeypatch.setattr(settings, "agent_internal_jwt_secret", SECRET)
    with pytest.raises(ValueError):
        # An anonymous call would be executed against an empty identity.
        mint_java_caller_token("   ")


# --------------------------------------------------------------------------- adapter


@pytest.mark.asyncio
async def test_ticket_create_posts_the_synthesized_arguments():
    client = FakeClient(FakeResponse(200, {"success": True, "text": "已创建", "data": {}}))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_create", {"latest_user_message": "打印机坏了"}, state())

    assert result["success"] is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "http://java.test:8080/v1/internal/tools/ticket_create"
    assert call["headers"]["Authorization"].startswith("Bearer ")
    args = call["json"]["args"]
    assert args["title"] == "打印机坏了"
    assert args["description"] == "打印机坏了"
    assert args["type"] == "incident"
    # The conversation is carried so a ticket raised from chat stays traceable.
    assert args["conversationId"] == "conv-9"
    # Never chosen here: the impact/urgency matrix owns priority.
    assert "priority" not in args


@pytest.mark.asyncio
async def test_an_unreachable_java_service_is_a_failure_never_a_fabricated_success():
    client = FakeClient(error=httpx.ConnectError("refused"))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_create", {}, state())

    assert result["success"] is False
    assert result["error"] == TICKET_SERVICE_UNAVAILABLE
    assert "未执行" in result["text"]


@pytest.mark.asyncio
async def test_a_rejected_caller_identity_is_reported_as_a_configuration_problem():
    client = FakeClient(FakeResponse(401, {"error": "INVALID_INTERNAL_CALLER"}))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_list", {}, state())

    assert result["success"] is False
    assert result["error"] == "INVALID_INTERNAL_CALLER"
    assert "密钥" in result["text"]


@pytest.mark.asyncio
async def test_a_missing_secret_refuses_instead_of_calling(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_jwt_secret", "")
    client = FakeClient(FakeResponse(200, {"success": True}))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_create", {}, state())

    assert result["success"] is False
    assert result["error"] == CALLER_TOKEN_UNAVAILABLE
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_unparsable_body_is_a_failure():
    client = FakeClient(FakeResponse(200, raw="not json"))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_list", {}, state())

    assert result["success"] is False


@pytest.mark.asyncio
async def test_a_server_error_is_reported_with_its_status():
    client = FakeClient(FakeResponse(500, {"error": "INTERNAL_ERROR"}))
    adapter = adapter_with(client)

    result = await adapter.execute("ticket_list", {}, state())

    assert result["success"] is False
    assert "500" in result["text"]


@pytest.mark.asyncio
async def test_the_200_body_is_passed_through():
    body = {"success": True, "text": "共 1 张工单：\n#12 [new] 打印机", "data": {"total": 1}}
    client = FakeClient(FakeResponse(200, body))

    result = await adapter_with(client).execute("ticket_list", {"limit": 5}, state())

    assert result["text"] == body["text"]
    assert result["data"] == {"total": 1}


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
async def test_routing_sends_tickets_to_the_real_adapter_and_marks_the_rest_simulated():
    client = FakeClient(FakeResponse(200, {"success": True, "text": "ok", "data": {}}))
    router = RoutingToolAdapter(
        ticket_adapter=adapter_with(client),
        fallback=MockToolAdapter(),
    )

    real = await router.execute("ticket_list", {}, state())
    assert client.calls, "the ticket tool must reach the HTTP adapter"
    assert "simulated" not in real

    stubbed = await router.execute("idp", {"latest_user_message": "重置密码"}, state())
    # The stub must advertise itself, so nothing downstream can mistake it for an integration.
    assert stubbed.get("simulated") is True


def test_tool_status_separates_real_from_mock():
    status = tool_status()

    # Four adapters exist; only two are reachable from chat today, and the report says so
    # rather than implying all four are wired.
    assert set(status["implemented"]) == set(TICKET_TOOLS)
    assert set(status["declared_real"]) == {"ticket_create", "ticket_list"}
    assert set(status["implemented_not_wired"]) == {"ticket_get", "ticket_comment"}
    assert "idp" in status["declared_mock"]
    assert "mdm" in status["declared_mock"]
    # There is no model function-calling in this service; saying otherwise would be a lie.
    assert status["routing"] == "rule-based"
    assert status["java_service_configured"] is True


# --------------------------------------------------------------------------- selection


def test_a_fault_raises_a_ticket():
    assert "ticket_create" in select_tools_for_request("it", "我的打印机坏了")


def test_a_status_question_lists_tickets_instead_of_creating_one():
    selected = select_tools_for_request("it", "我的工单处理到哪了？")
    assert "ticket_list" in selected
    assert "ticket_create" not in selected


def test_a_ticket_number_is_looked_up():
    assert "ticket_list" in select_tools_for_request("it", "帮我看看 #12")
    assert "ticket_create" not in select_tools_for_request("it", "帮我看看 #12")


def test_unrelated_messages_do_not_touch_tickets():
    selected = select_tools_for_request("it", "今天天气怎么样")
    assert not (set(selected) & set(TICKET_TOOLS))


def test_capabilities_action_never_selects_ticket_tools():
    selected = select_tools_for_request("it", "打印机坏了", action="capabilities")
    assert not (set(selected) & set(TICKET_TOOLS))


def test_auto_creation_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ticket_auto_create_from_chat", False)
    assert ticket_tools_for_message("打印机坏了", list(TICKET_TOOLS)) == []
    # Listing still works: the switch only governs automatic creation.
    assert "ticket_list" in ticket_tools_for_message("我的工单", list(TICKET_TOOLS))


def test_agents_without_the_tool_never_get_it():
    # The approval and guardian agents have no ticket tools declared.
    assert not (
        set(select_tools_for_request("approval", "打印机坏了")) & set(TICKET_TOOLS)
    )
