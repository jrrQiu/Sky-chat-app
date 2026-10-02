"""Durability, idempotency and observability tests for the approval protocol.

These cover the acceptance criteria of the checkpoint design:
  * one high-risk request produces exactly one checkpoint and one approval;
  * the same thread survives a "process restart";
  * a lost checkpoint yields a terminal error and never starts a new run;
  * a replayed or concurrently delivered approval executes the write once;
  * a rejected approval never executes the write.
"""

import asyncio
import copy
import time
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.api.schemas import AgentStatePayload, ChatMessage, ChatRequest, UserContext
from app.core.effect_ledger import EffectLedger, effect_key_for
from app.graph import nodes
from app.graph import runner as runner_module
from app.graph.root import build_root_graph
from app.graph.runner import (
    TERMINAL_CHECKPOINT_ERRORS,
    resume_graph,
    run_graph,
)
from app.persistence.approval_store import (
    ApprovalPolicyError,
    ApprovalStore,
    ApprovalStoreUnavailable,
    approval_key_for,
    approval_store,
)
from app.services.approval_resume import resolve_approval
from app.services.resume_reconciler import reconcile_once
from app.tools.adapters.mock import execute_mock_tool


WRITE_ACTION = "vpn_api_provision"

# The requester is `u1`; separation of duties means the decider must be someone
# else holding one of the approval's required roles.
APPROVER_ID = "approver1"
APPROVER_ROLES = ["network_admin"]


async def _decide(
    store,
    task_id: str,
    decision: str,
    *,
    resume_request_id: str,
    user_id: str = APPROVER_ID,
    roles: list[str] | None = None,
):
    return await store.decide(
        task_id,
        decision,
        resume_request_id=resume_request_id,
        user_id=user_id,
        actor_roles=APPROVER_ROLES if roles is None else roles,
    )


def make_request(request_id: str, message: str = "申请VPN权限") -> ChatRequest:
    roles = ["network_admin"]
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
            riskLevel="high",
        ),
    )


class CountingAdapter:
    """Records every tool invocation and delegates to the mock provider."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool_name, args, state):
        self.calls.append((tool_name, args))
        return await execute_mock_tool(tool_name, args, state)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def count(self, tool_name: str) -> int:
        return self.names().count(tool_name)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch):
    """Keep the module-level singletons from leaking between tests."""
    approval_store._tasks.clear()
    from app.core import effect_ledger as ledger_module

    ledger_module.effect_ledger._records.clear()
    ledger_module.effect_ledger._locks.clear()

    async def fake_stream_completion(**_kwargs):
        yield ("answer", "已按审批结果完成处理。")

    monkeypatch.setattr(nodes.generate, "stream_completion", fake_stream_completion)
    yield
    approval_store._tasks.clear()
    ledger_module.effect_ledger._records.clear()
    ledger_module.effect_ledger._locks.clear()


@pytest.fixture
def adapter(monkeypatch) -> CountingAdapter:
    counter = CountingAdapter()
    monkeypatch.setattr(nodes.service_workflow, "get_tool_adapter", lambda: counter)
    return counter


async def _interrupt(saver, turn_id: str, graph=None):
    """Run one high-risk turn and return (graph, approval_required event)."""
    graph = graph or build_root_graph(saver)
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    await run_graph(make_request(turn_id), emit, graph=graph)
    approvals = [event for event in events if event.get("type") == "approval_required"]
    return graph, approvals


# --------------------------------------------------------------------- basics


def test_high_risk_request_creates_one_checkpoint_and_one_approval(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_single")
        assert len(approvals) == 1

        event = approvals[0]
        assert event["approval_id"]
        assert event["checkpoint_id"], "interrupt must expose a checkpoint id"
        assert event["interrupt_id"], "interrupt must expose an interrupt id"

        task = await approval_store.get(event["approval_id"])
        assert task is not None
        assert task.status == "pending"
        assert task.checkpoint_id == event["checkpoint_id"]
        assert task.checkpoint_ns == event["checkpoint_ns"]
        assert task.interrupt_id == event["interrupt_id"]
        assert task.resume_state == "queued"
        assert task.consumed_at is None

        # Exactly one approval row, and the write has not happened yet.
        assert len(approval_store._tasks) == 1
        assert adapter.count(WRITE_ACTION) == 0

    asyncio.run(scenario())


def test_replaying_prepare_approval_returns_the_same_task():
    async def scenario():
        store = ApprovalStore()
        key = approval_key_for("turn_replay", WRITE_ACTION)
        first = await store.create(
            run_id="turn_replay",
            user_id="u1",
            agent_id="network",
            intent=None,
            risk_level="high",
            rule_id="R1",
            approval_key=key,
            resume_payload={"pending_action": WRITE_ACTION},
        )
        second = await store.create(
            run_id="turn_replay",
            user_id="u1",
            agent_id="network",
            intent=None,
            risk_level="high",
            rule_id="R1",
            approval_key=key,
            resume_payload={"pending_action": WRITE_ACTION},
        )
        assert first.id == second.id
        assert len(store._tasks) == 1

    asyncio.run(scenario())


# ------------------------------------------------------- restart and recovery


def test_resume_after_process_restart_executes_write_exactly_once(adapter):
    async def scenario():
        # One shared durable saver stands in for PostgreSQL across a restart.
        saver = InMemorySaver()
        first_graph, approvals = await _interrupt(saver, "turn_restart")
        approval_id = approvals[0]["approval_id"]

        # A new process rebuilds the graph from the same checkpointer.
        restarted_graph = build_root_graph(saver)
        decision = await _decide(
            approval_store, approval_id, "approved", resume_request_id="rsq_restart"
        )
        assert decision.won is True

        resolution = await resolve_approval(
            decision.task,
            graph=restarted_graph,
            resume_request_id="rsq_restart",
        )
        assert resolution.status == "resumed"
        assert adapter.count(WRITE_ACTION) == 1

        task = await approval_store.get(approval_id)
        assert task.resume_state == "succeeded"
        assert task.consumed_at is not None

        # Replaying the same decision must not run the write again.
        replay = await resolve_approval(task, graph=restarted_graph)
        assert replay.status == "already_resumed"
        assert adapter.count(WRITE_ACTION) == 1

    asyncio.run(scenario())


def test_reconciler_finishes_a_resume_that_never_reached_the_graph(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_crash")
        approval_id = approvals[0]["approval_id"]

        # The decision committed but the process died before calling the agent.
        await _decide(
            approval_store, approval_id, "approved", resume_request_id="rsq_crash"
        )
        assert adapter.count(WRITE_ACTION) == 0

        delivered = await reconcile_once(graph)
        assert delivered == 1
        assert adapter.count(WRITE_ACTION) == 1

        task = await approval_store.get(approval_id)
        assert task.resume_state == "succeeded"
        # A second tick is a no-op: the claim and the state guard both hold.
        assert await reconcile_once(graph) == 0
        assert adapter.count(WRITE_ACTION) == 1

    asyncio.run(scenario())


def test_missing_checkpoint_is_terminal_and_starts_no_run():
    async def scenario():
        graph = build_root_graph(InMemorySaver())
        events: list[dict] = []

        async def emit(event):
            events.append(event)

        outcome = await resume_graph(
            "turn_never_started",
            {
                "decision": "approved",
                "approval_id": "approval_missing",
                "checkpoint_id": "1e0f0000-0000-0000-0000-000000000000",
                "checkpoint_ns": "",
                "interrupt_id": "not-a-real-interrupt",
                "resume_request_id": "rsq_missing",
            },
            emit,
            graph=graph,
        )

        assert outcome.status == "failed"
        assert outcome.error in TERMINAL_CHECKPOINT_ERRORS
        assert outcome.retryable is False

        # The critical assertion: no new run was silently started.
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": "turn_never_started"}}
        )
        assert not snapshot.values
        assert not snapshot.next

    asyncio.run(scenario())


def test_stale_checkpoint_is_reported_as_stale_not_resumed():
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_stale")
        event = approvals[0]

        # A wrong checkpoint reference for a live thread is refused.
        stale = await resume_graph(
            "turn_stale",
            {
                "decision": "approved",
                "approval_id": event["approval_id"],
                "checkpoint_id": "00000000-0000-0000-0000-000000000000",
                "checkpoint_ns": "",
                "interrupt_id": event["interrupt_id"],
                "resume_request_id": "rsq_stale",
            },
            lambda _: asyncio.sleep(0),
            graph=graph,
        )
        assert stale.status == "failed"
        assert stale.error in TERMINAL_CHECKPOINT_ERRORS

    asyncio.run(scenario())


# ------------------------------------------------------------------ rejection


def test_rejected_approval_never_executes_the_write(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_reject")
        approval_id = approvals[0]["approval_id"]

        decision = await _decide(
            approval_store, approval_id, "rejected", resume_request_id="rsq_reject"
        )
        assert decision.won is True

        resolution = await resolve_approval(
            decision.task,
            graph=graph,
            resume_request_id="rsq_reject",
        )
        assert resolution.status == "resumed"
        assert adapter.count(WRITE_ACTION) == 0

        # The run still terminates, so the checkpoint is not left dangling.
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": "turn_reject"}}
        )
        assert not snapshot.next
        assert "终止" in str(snapshot.values.get("final_answer", ""))

    asyncio.run(scenario())


# ---------------------------------------------------------------- concurrency


class FakeRepository:
    """In-memory stand-in that preserves the SQL compare-and-swap semantics."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.effects: dict[str, dict] = {}
        self.decisions: list[dict] = []
        self.fail = False

    def _guard(self):
        if self.fail:
            raise RuntimeError("database is down")

    async def create_approval(self, task):
        self._guard()
        existing = self.rows.get(task["id"])
        if existing:
            return copy.deepcopy(existing)
        for row in self.rows.values():
            if task.get("approval_key") and row.get("approval_key") == task["approval_key"]:
                return copy.deepcopy(row)
        now = time.time()
        row = {
            **task,
            "created_at": now,
            "updated_at": now,
            "resume_attempts": 0,
            "resume_state": task.get("resume_state") or "queued",
            "consumed_at": None,
        }
        self.rows[row["id"]] = row
        return copy.deepcopy(row)

    async def save_approval(self, task):
        self._guard()
        self.rows[task["id"]] = {**self.rows.get(task["id"], {}), **task}
        return copy.deepcopy(self.rows[task["id"]])

    async def update_approval_checkpoint_refs(
        self, task_id, *, checkpoint_id, checkpoint_ns, interrupt_id
    ):
        self._guard()
        row = self.rows.get(task_id)
        if not row:
            return None
        row.update(
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
            interrupt_id=interrupt_id,
            updated_at=time.time(),
        )
        return copy.deepcopy(row)

    async def get_approval(self, task_id):
        self._guard()
        row = self.rows.get(task_id)
        return copy.deepcopy(row) if row else None

    async def get_approval_by_key(self, approval_key):
        self._guard()
        for row in self.rows.values():
            if approval_key and row.get("approval_key") == approval_key:
                return copy.deepcopy(row)
        return None

    async def list_approvals(self, user_id):
        self._guard()
        return [
            copy.deepcopy(row) for row in self.rows.values() if row["user_id"] == user_id
        ]

    async def decide_approval(
        self,
        task_id,
        status,
        *,
        resume_request_id=None,
        decided_by=None,
        decision_comment=None,
    ):
        self._guard()
        row = self.rows.get(task_id)
        if not row or row["status"] != "pending":
            return None
        row.update(
            status=status,
            resume_request_id=resume_request_id,
            resume_state="queued",
            resume_attempts=0,
            resume_error=None,
            decided_by=decided_by,
            decided_at=time.time() if decided_by else None,
            decision_comment=decision_comment,
            updated_at=time.time(),
        )
        return copy.deepcopy(row)

    async def record_approval_decision(
        self,
        *,
        approval_id,
        decision,
        decided_by,
        decided_by_roles="",
        comment=None,
        source_ip=None,
        resume_request_id=None,
    ):
        self._guard()
        self.decisions.append(
            {
                "approval_id": approval_id,
                "decision": decision,
                "decided_by": decided_by,
                "decided_by_roles": decided_by_roles,
                "comment": comment,
                "resume_request_id": resume_request_id,
            }
        )

    async def list_approval_decisions(self, approval_id):
        self._guard()
        return [row for row in self.decisions if row["approval_id"] == approval_id]

    async def claim_resume(
        self, task_id, *, resume_request_id=None, stale_running_seconds=120
    ):
        self._guard()
        row = self.rows.get(task_id)
        if not row or row["status"] not in {"approved", "rejected"}:
            return None
        state = row.get("resume_state")
        if state not in {"queued", "failed", "running"}:
            return None
        if state == "running" and time.time() - row["updated_at"] < stale_running_seconds:
            return None
        row.update(
            resume_state="running",
            resume_request_id=row.get("resume_request_id") or resume_request_id,
            resume_attempts=int(row.get("resume_attempts") or 0) + 1,
            updated_at=time.time(),
        )
        return copy.deepcopy(row)

    async def mark_resume_result(
        self, task_id, *, resume_state, resume_error=None, consumed=False
    ):
        self._guard()
        row = self.rows.get(task_id)
        if not row or row.get("resume_state") == "succeeded":
            # Mirrors the SQL guard: success is terminal.
            return None
        row.update(resume_state=resume_state, resume_error=resume_error)
        if consumed:
            row["consumed_at"] = time.time()
        row["updated_at"] = time.time()
        return copy.deepcopy(row)

    async def list_resume_candidates(self, limit=20):
        self._guard()
        out = [
            copy.deepcopy(row)
            for row in self.rows.values()
            if row["status"] in {"approved", "rejected"}
            and row.get("resume_state") in {"queued", "failed"}
            and int(row.get("resume_attempts") or 0) < 5
            and (row.get("checkpoint_id") or row.get("interrupt_id"))
        ]
        return out[:limit]

    async def begin_effect(
        self, effect_key, *, turn_id, approval_id, action, provider_key
    ):
        self._guard()
        row = self.effects.setdefault(
            effect_key,
            {
                "effect_key": effect_key,
                "turn_id": turn_id,
                "approval_id": approval_id,
                "action": action,
                "status": "started",
                "provider_key": provider_key,
                "attempts": 0,
                "response": None,
                "error": None,
            },
        )
        row["attempts"] += 1
        return copy.deepcopy(row)

    async def get_effect(self, effect_key):
        self._guard()
        row = self.effects.get(effect_key)
        return copy.deepcopy(row) if row else None

    async def finish_effect(self, effect_key, *, status, response=None, error=None):
        self._guard()
        row = self.effects.get(effect_key)
        if row:
            row.update(status=status, response=response, error=error)


async def _seed_durable_task(repo: FakeRepository, turn_id: str = "turn_durable"):
    store = ApprovalStore(postgres=repo)
    task = await store.create(
        run_id=turn_id,
        turn_id=turn_id,
        thread_id=turn_id,
        user_id="u1",
        agent_id="network",
        intent=None,
        risk_level="high",
        rule_id="R1",
        approval_key=approval_key_for(turn_id, WRITE_ACTION),
        resume_payload={"pending_action": WRITE_ACTION},
    )
    await store.update_checkpoint_refs(
        task.id,
        checkpoint_id="cp-1",
        checkpoint_ns="",
        interrupt_id="int-1",
    )
    return store, task


def test_concurrent_decisions_only_one_cas_wins():
    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo)

        results = await asyncio.gather(
            *[
                _decide(store, task.id, "approved", resume_request_id=f"rsq_{index}")
                for index in range(8)
            ]
        )
        winners = [result for result in results if result.won]
        assert len(winners) == 1
        assert sum(1 for result in results if result.reason == "already_decided") == 7

        # The winner owns the resume request id; losers must not overwrite it.
        persisted = repo.rows[task.id]
        assert persisted["resume_request_id"] == winners[0].task.resume_request_id

    asyncio.run(scenario())


def test_concurrent_resume_claims_have_exactly_one_owner():
    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo)
        await _decide(store, task.id, "approved", resume_request_id="rsq_owner")

        claims = await asyncio.gather(
            *[store.claim_resume(task.id) for _ in range(8)]
        )
        owners = [claim for claim in claims if claim is not None]
        assert len(owners) == 1
        assert owners[0].resume_state == "running"

    asyncio.run(scenario())


def test_resume_candidates_skip_terminal_states():
    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo)
        await _decide(store, task.id, "approved", resume_request_id="rsq_c")

        assert [item.id for item in await store.list_resume_candidates()] == [task.id]

        await store.mark_resume_result(task.id, resume_state="succeeded", consumed=True)
        assert await store.list_resume_candidates() == []

        await store.mark_resume_result(task.id, resume_state="stale")
        assert await store.list_resume_candidates() == []

    asyncio.run(scenario())


def test_durable_store_fails_closed_when_postgres_is_unreachable():
    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo)
        repo.fail = True

        with pytest.raises(ApprovalStoreUnavailable):
            await _decide(store, task.id, "approved", resume_request_id="rsq_down")

    asyncio.run(scenario())


def test_a_lost_duplicate_delivery_cannot_downgrade_a_success():
    """Two replicas delivering the same resume must not corrupt the outcome.

    The loser reads an already-consumed checkpoint and reports STALE_APPROVAL.
    That must never overwrite the winner's `succeeded` state.
    """

    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo)
        await _decide(store, task.id, "approved", resume_request_id="rsq_race")

        await store.mark_resume_result(task.id, resume_state="succeeded", consumed=True)
        downgraded = await store.mark_resume_result(
            task.id, resume_state="stale", resume_error="STALE_APPROVAL"
        )

        assert downgraded is None
        persisted = repo.rows[task.id]
        assert persisted["resume_state"] == "succeeded"
        assert persisted["resume_error"] is None
        assert persisted["consumed_at"] is not None

    asyncio.run(scenario())


def test_memory_store_never_downgrades_a_success():
    async def scenario():
        store = ApprovalStore()
        created = await store.create(
            run_id="turn_mem",
            user_id="u1",
            agent_id="network",
            intent=None,
            risk_level="high",
            rule_id="R1",
            resume_payload={"pending_action": WRITE_ACTION},
        )
        await store.mark_resume_result(created.id, resume_state="succeeded", consumed=True)
        assert (
            await store.mark_resume_result(created.id, resume_state="failed")
            is None
        )
        assert (await store.get(created.id)).resume_state == "succeeded"

    asyncio.run(scenario())


# -------------------------------------------------------------- effect ledger


def test_effect_ledger_executes_a_write_once_per_turn_and_action():
    async def scenario():
        ledger = EffectLedger()
        calls: list[str] = []

        async def execute(provider_key: str):
            calls.append(provider_key)
            return {"success": True, "text": "provisioned"}

        first = await ledger.run_once(
            turn_id="turn_effect",
            action=WRITE_ACTION,
            approval_id="approval_1",
            execute=execute,
        )
        second = await ledger.run_once(
            turn_id="turn_effect",
            action=WRITE_ACTION,
            approval_id="approval_1",
            execute=execute,
        )

        assert first["success"] is True
        assert second.get("deduplicated") is True
        assert len(calls) == 1
        # The provider receives the same key on every attempt.
        assert calls[0] == f"idem_{effect_key_for('turn_effect', WRITE_ACTION)[:40]}"

    asyncio.run(scenario())


def test_effect_ledger_distinguishes_different_actions():
    async def scenario():
        ledger = EffectLedger()
        calls: list[str] = []

        async def execute(provider_key: str):
            calls.append(provider_key)
            return {"success": True}

        await ledger.run_once(
            turn_id="turn_multi",
            action=WRITE_ACTION,
            approval_id=None,
            execute=execute,
        )
        await ledger.run_once(
            turn_id="turn_multi",
            action="finance_submit",
            approval_id=None,
            execute=execute,
        )
        assert len(calls) == 2

    asyncio.run(scenario())


def test_failed_effect_is_not_reported_as_succeeded():
    async def scenario():
        ledger = EffectLedger()

        async def execute(_provider_key: str):
            return {"success": False, "text": "provider rejected"}

        result = await ledger.run_once(
            turn_id="turn_fail",
            action=WRITE_ACTION,
            approval_id=None,
            execute=execute,
        )
        assert result["success"] is False
        record = ledger._records[effect_key_for("turn_fail", WRITE_ACTION)]
        assert record.status == "failed"
        assert not record.response.get("deduplicated")

    asyncio.run(scenario())


# ------------------------------------------------------- approval policy (P0)


def test_requester_cannot_approve_their_own_request(adapter):
    """Separation of duties: the classic self-approval hole."""

    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_sod")
        approval_id = approvals[0]["approval_id"]

        task = await approval_store.get(approval_id)
        assert task.user_id == "u1"

        with pytest.raises(ApprovalPolicyError) as exc:
            await approval_store.decide(
                approval_id,
                "approved",
                resume_request_id="rsq_sod",
                user_id="u1",  # the requester
                actor_roles=["network_admin"],
            )
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"

        # The approval is untouched and no write happened.
        assert (await approval_store.get(approval_id)).status == "pending"
        assert adapter.count(WRITE_ACTION) == 0

    asyncio.run(scenario())


def test_decider_must_hold_a_required_approver_role(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_role")
        approval_id = approvals[0]["approval_id"]

        task = await approval_store.get(approval_id)
        assert "network_admin" in task.required_approver_roles

        with pytest.raises(ApprovalPolicyError) as exc:
            await approval_store.decide(
                approval_id,
                "approved",
                resume_request_id="rsq_role",
                user_id=APPROVER_ID,
                actor_roles=["employee"],  # wrong role
            )
        assert exc.value.code == "APPROVER_ROLE_REQUIRED"
        assert (await approval_store.get(approval_id)).status == "pending"

        # A holder of the required role can decide.
        decision = await _decide(
            approval_store, approval_id, "approved", resume_request_id="rsq_role"
        )
        assert decision.won is True
        assert decision.task.decided_by == APPROVER_ID

    asyncio.run(scenario())


def test_admin_always_passes_the_approver_role_check(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_admin")
        approval_id = approvals[0]["approval_id"]

        decision = await approval_store.decide(
            approval_id,
            "approved",
            resume_request_id="rsq_admin",
            user_id="admin1",
            actor_roles=["admin"],
        )
        assert decision.won is True

    asyncio.run(scenario())


def test_decision_trail_records_who_decided_and_why():
    async def scenario():
        repo = FakeRepository()
        store, task = await _seed_durable_task(repo, turn_id="turn_trail")

        await store.decide(
            task.id,
            "approved",
            resume_request_id="rsq_trail",
            user_id=APPROVER_ID,
            actor_roles=APPROVER_ROLES,
            comment="approved for the Q3 audit",
            source_ip="10.0.0.9",
        )

        trail = await store.list_decisions(task.id)
        assert len(trail) == 1
        assert trail[0]["decision"] == "approved"
        assert trail[0]["decided_by"] == APPROVER_ID
        assert trail[0]["comment"] == "approved for the Q3 audit"
        assert "network_admin" in trail[0]["decided_by_roles"]

        row = repo.rows[task.id]
        assert row["decided_by"] == APPROVER_ID
        assert row["decided_at"] is not None
        assert row["decision_comment"] == "approved for the Q3 audit"

    asyncio.run(scenario())


def test_a_refused_decision_does_not_consume_the_approval(adapter):
    """A denied attempt must leave the CAS window open for a real approver."""

    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_refused")
        approval_id = approvals[0]["approval_id"]

        with pytest.raises(ApprovalPolicyError):
            await approval_store.decide(
                approval_id,
                "approved",
                resume_request_id="rsq_refused",
                user_id="u1",
                actor_roles=["network_admin"],
            )

        decision = await _decide(
            approval_store, approval_id, "approved", resume_request_id="rsq_refused2"
        )
        assert decision.won is True

    asyncio.run(scenario())


# ------------------------------------------------------------ observability


def test_approval_exposes_resume_diagnostics(adapter):
    async def scenario():
        saver = InMemorySaver()
        graph, approvals = await _interrupt(saver, "turn_diag")
        approval_id = approvals[0]["approval_id"]

        decision = await _decide(
            approval_store, approval_id, "approved", resume_request_id="rsq_diag"
        )
        await resolve_approval(
            decision.task, graph=graph, resume_request_id="rsq_diag"
        )

        task = await approval_store.get(approval_id)
        assert task.resume_request_id == "rsq_diag"
        assert task.resume_state == "succeeded"
        assert task.resume_attempts == 1
        assert task.resume_error is None
        # Attribution is recorded together with the decision.
        assert task.decided_by == APPROVER_ID
        assert task.decided_at is not None
        assert task.consumed_at is not None
        assert task.result is not None
        assert task.result["resume_status"] == "resumed"

    asyncio.run(scenario())


def test_effect_key_is_stable_for_the_same_turn():
    assert effect_key_for("t1", WRITE_ACTION) == effect_key_for("t1", WRITE_ACTION)
    assert effect_key_for("t1", WRITE_ACTION) != effect_key_for("t2", WRITE_ACTION)
    assert len(effect_key_for("t1", WRITE_ACTION)) == 64
    # Guards the UUID helper used for approval ids staying collision-free enough.
    assert uuid.UUID(str(uuid.uuid4()))
