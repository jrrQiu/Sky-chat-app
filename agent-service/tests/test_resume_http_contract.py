"""HTTP-level contract tests for the resume endpoint.

These pin the wire behaviour the Java service depends on: content negotiation,
the exact 409 codes that make a retry illegal, and the JSON resume flow that
consumes an interrupt created through the real chat endpoint.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.persistence.approval_store import approval_store
from tests.internal_auth import auth_headers, mint_token


def _headers(accept: str = "application/json", user: str = "u1") -> dict[str, str]:
    # A signed internal JWT is the only accepted caller identity.
    return auth_headers(user, ["network_admin"], accept=accept)


APPROVER = "approver1"


def _high_risk_payload(turn_id: str) -> dict:
    return {
        "request_id": turn_id,
        "user_context": {"userId": "u1", "roles": ["network_admin"]},
        "conversation_id": "c1",
        "user_message_id": "m1",
        "assistant_message_id": "m2",
        "messages": [{"role": "user", "content": "申请VPN权限"}],
        "latest_user_message": "申请VPN权限",
        "agent_state": {
            "requestId": turn_id,
            "userContext": {"userId": "u1", "roles": ["network_admin"]},
            "riskLevel": "high",
        },
    }


@pytest.fixture(autouse=True)
def _clean_store():
    approval_store._tasks.clear()
    yield
    approval_store._tasks.clear()


@pytest.fixture
def client(monkeypatch):
    # The graph runs without a live model in these tests.
    from app.graph import nodes

    async def fake_stream_completion(**_kwargs):
        yield ("answer", "已完成。")

    monkeypatch.setattr(nodes.generate, "stream_completion", fake_stream_completion)
    with TestClient(create_app()) as test_client:
        yield test_client


def _read_sse(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line if isinstance(line, str) else line.decode("utf-8")
        if text.startswith("data:"):
            events.append(json.loads(text[len("data:"):].strip()))
    return events


def test_missing_checkpoint_returns_409_without_starting_a_run(client):
    response = client.post(
        "/v1/workflows/turn_absent/resume",
        json={
            "decision": "approved",
            "approval_id": "approval_x",
            "checkpoint_id": "00000000-0000-0000-0000-000000000000",
            "checkpoint_ns": "",
            "interrupt_id": "int_x",
            "resume_request_id": "rsq_x",
        },
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] in {
        "STALE_APPROVAL",
        "CHECKPOINT_NOT_FOUND",
    }


def test_resume_without_checkpoint_reference_is_rejected(client):
    response = client.post(
        "/v1/workflows/turn_absent/resume",
        json={"decision": "approved", "approval_id": "approval_x"},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "MISSING_CHECKPOINT_REFERENCE"


def test_full_approval_round_trip_over_http(client):
    turn_id = "turn_http"

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json=_high_risk_payload(turn_id),
        headers=_headers(accept="text/event-stream"),
    ) as stream:
        assert stream.status_code == 200
        events = _read_sse(stream)

    approval_events = [e for e in events if e.get("type") == "approval_required"]
    assert len(approval_events) == 1

    event = approval_events[0]
    assert event["checkpoint_id"] and event["interrupt_id"]
    assert len(approval_store._tasks) == 1

    response = client.post(
        f"/v1/workflows/{turn_id}/resume",
        json={
            "decision": "approved",
            "approval_id": event["approval_id"],
            "checkpoint_id": event["checkpoint_id"],
            "checkpoint_ns": event["checkpoint_ns"],
            "interrupt_id": event["interrupt_id"],
            "resume_request_id": "rsq_http",
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resumed"
    assert any(e.get("type") == "complete" for e in body["events"])

    # The write happened and the checkpoint is consumed, so a second delivery is
    # refused instead of re-running it.
    task = asyncio.run(approval_store.get(event["approval_id"]))
    assert task.resume_state == "succeeded"
    assert task.consumed_at is not None

    replay = client.post(
        f"/v1/workflows/{turn_id}/resume",
        json={
            "decision": "approved",
            "approval_id": event["approval_id"],
            "checkpoint_id": event["checkpoint_id"],
            "checkpoint_ns": event["checkpoint_ns"],
            "interrupt_id": event["interrupt_id"],
            "resume_request_id": "rsq_http",
        },
        headers=_headers(),
    )
    assert replay.status_code == 409


def test_sse_resume_still_streams_for_the_frontend(client):
    turn_id = "turn_sse"

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json=_high_risk_payload(turn_id),
        headers=_headers(accept="text/event-stream"),
    ) as stream:
        events = _read_sse(stream)

    event = [e for e in events if e.get("type") == "approval_required"][0]

    with client.stream(
        "POST",
        f"/v1/workflows/{turn_id}/resume",
        json={
            "decision": "rejected",
            "approval_id": event["approval_id"],
            "checkpoint_id": event["checkpoint_id"],
            "checkpoint_ns": event["checkpoint_ns"],
            "interrupt_id": event["interrupt_id"],
            "resume_request_id": "rsq_sse",
        },
        headers=_headers(accept="text/event-stream"),
    ) as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        sse_events = _read_sse(stream)

    assert any(e.get("type") == "complete" for e in sse_events)


def test_rejection_over_http_does_not_execute_the_write(client):
    turn_id = "turn_http_reject"

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json=_high_risk_payload(turn_id),
        headers=_headers(accept="text/event-stream"),
    ) as stream:
        events = _read_sse(stream)

    event = [e for e in events if e.get("type") == "approval_required"][0]

    response = client.post(
        f"/v1/workflows/{turn_id}/resume",
        json={
            "decision": "rejected",
            "approval_id": event["approval_id"],
            "checkpoint_id": event["checkpoint_id"],
            "checkpoint_ns": event["checkpoint_ns"],
            "interrupt_id": event["interrupt_id"],
            "resume_request_id": "rsq_reject_http",
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    names = [
        e.get("name")
        for e in response.json()["events"]
        if e.get("type") == "tool_call"
    ]
    assert "vpn_api_provision" not in names


def test_internal_approval_decision_route_is_idempotent(client):
    turn_id = "turn_internal"

    with client.stream(
        "POST",
        "/v1/chat/stream",
        json=_high_risk_payload(turn_id),
        headers=_headers(accept="text/event-stream"),
    ) as stream:
        events = _read_sse(stream)

    approval_id = [e for e in events if e.get("type") == "approval_required"][0][
        "approval_id"
    ]

    # The requester (u1) is refused by separation of duties...
    self_approval = client.patch(
        f"/v1/approvals/{approval_id}/decision",
        json={"action": "approve"},
        headers=_headers(),  # u1, the requester
    )
    assert self_approval.status_code == 403
    assert self_approval.json()["detail"] == "SELF_APPROVAL_FORBIDDEN"

    # ...and so is a decider without one of the required roles.
    wrong_role = client.patch(
        f"/v1/approvals/{approval_id}/decision",
        json={"action": "approve"},
        headers=auth_headers(APPROVER, ["employee"]),
    )
    assert wrong_role.status_code == 403
    assert wrong_role.json()["detail"] == "APPROVER_ROLE_REQUIRED"

    # A different user holding the required role can decide.
    first = client.patch(
        f"/v1/approvals/{approval_id}/decision",
        json={
            "action": "approve",
            "resume_request_id": "rsq_internal",
            "comment": "approved after checking the request",
        },
        headers=_headers(user=APPROVER),
    )
    assert first.status_code == 200
    assert first.json()["resume"]["status"] == "resumed"
    # Attribution travels with the decision.
    assert first.json()["approval"]["decided_by"] == APPROVER
    assert first.json()["approval"]["decided_at"] is not None
    assert "network_admin" in first.json()["approval"]["required_approver_roles"]

    second = client.patch(
        f"/v1/approvals/{approval_id}/decision",
        json={"action": "approve", "resume_request_id": "rsq_internal"},
        headers=_headers(user=APPROVER),
    )
    assert second.status_code == 200
    assert second.json()["resume"]["status"] == "already_resumed"

    conflicting = client.patch(
        f"/v1/approvals/{approval_id}/decision",
        json={"action": "reject"},
        headers=_headers(user=APPROVER),
    )
    assert conflicting.status_code == 409

    trail = client.get(
        f"/v1/approvals/{approval_id}/decisions",
        headers=_headers(user=APPROVER),
    )
    assert trail.status_code == 200
    decisions = trail.json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decided_by"] == APPROVER
    assert decisions[0]["comment"] == "approved after checking the request"


def test_unsigned_caller_is_rejected(client):
    """An X-User-ID header alone is not an identity."""
    response = client.post(
        "/v1/chat/stream",
        json=_high_risk_payload("turn_unsigned"),
        headers={"X-User-ID": "admin", "Accept": "text/event-stream"},
    )
    assert response.status_code == 401


def test_token_signed_for_another_audience_is_rejected(client):
    forged = auth_headers("u1", ["network_admin"])
    forged["Authorization"] = (
        "Bearer " + mint_token("u1", ["admin"], audience="someone-else")
    )
    response = client.post(
        "/v1/chat/stream",
        json=_high_risk_payload("turn_wrong_aud"),
        headers=forged,
    )
    assert response.status_code == 401


def test_token_signed_with_the_wrong_secret_is_rejected(client):
    forged = auth_headers("u1", ["network_admin"])
    forged["Authorization"] = (
        "Bearer " + mint_token("u1", ["admin"], secret="not-the-shared-secret")
    )
    response = client.post(
        "/v1/chat/stream",
        json=_high_risk_payload("turn_forged"),
        headers=forged,
    )
    assert response.status_code == 401


def test_ready_endpoint_reports_durability(client):
    response = client.get("/ready")
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["graph"] is True
    assert checks["checkpoint_backend"] == "memory"
    assert checks["durable_checkpoints"] is False
    assert checks["caller_identity"] == "signed-jwt"
    assert checks["retrieval_rail"] is True
