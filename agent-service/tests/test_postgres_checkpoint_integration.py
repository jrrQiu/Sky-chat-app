"""Real PostgreSQL integration for the durable checkpoint + resume protocol.

Skipped automatically when the database from `docker-compose.yml` is not
reachable, so the unit suite still runs anywhere.

What this proves that the in-memory tests cannot:
  * `AsyncPostgresSaver` really stores the interrupt in the `agent_checkpoint`
    schema, including the `checkpoint_ns` the subgraph interrupt is raised in;
  * a resume works from a *separately opened* connection pool, which is what a
    process restart or a different replica looks like;
  * the pinned `checkpoint_id` + `checkpoint_ns` precheck succeeds against the
    database rather than against process memory;
  * the effect ledger records exactly one successful external write.
"""

import asyncio
import os

import psycopg
import pytest

from app.graph.nodes import generate as generate_node
from app.graph.nodes import service_workflow as service_workflow_node
from app.persistence.approval_store import approval_store

PG_URL = os.environ.get(
    "TEST_POSTGRES_URL",
    "postgresql://skychat:password123@localhost:5433/skychat",
)


def _pg_available() -> bool:
    try:
        with psycopg.connect(PG_URL, connect_timeout=3) as conn:
            conn.execute("select 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL is not reachable; run docker compose up -d first",
)

TURN_ID = "turn_pg_integration"
WRITE_ACTION = "vpn_api_provision"


def _run(scenario) -> None:
    """Run one scenario and close the pool in the same loop that opened it.

    `AsyncConnectionPool` binds its worker tasks to the loop it was created in,
    so closing it from a later `asyncio.run` cancels tasks owned by a dead loop.
    The service has a single long-lived loop and never hits this; the tests do.
    """
    from app.persistence import postgres_repository as repo_module

    async def wrapper():
        try:
            await scenario()
        finally:
            await repo_module.close_pool()
            repo_module._pool = None

    asyncio.run(wrapper())


@pytest.fixture
def pg_env():
    """Point the settings and the module-level singletons at the real database."""
    from app.config import settings
    from app.core import effect_ledger as ledger_module
    from app.persistence import postgres_repository as repo_module
    from app.persistence.postgres_repository import PostgresRepository

    original_url = settings.database_url
    original_backend = settings.checkpoint_backend
    settings.database_url = PG_URL
    settings.checkpoint_backend = "postgres"

    # A fresh pool per test, opened inside that test's own event loop.
    repo_module._pool = None
    repository = PostgresRepository()
    previous_approval = approval_store._postgres
    previous_ledger = ledger_module.effect_ledger._postgres
    approval_store._postgres = repository
    ledger_module.effect_ledger._postgres = repository
    approval_store._tasks.clear()
    ledger_module.effect_ledger._records.clear()

    _cleanup()

    try:
        yield repository
    finally:
        _cleanup()
        approval_store._postgres = previous_approval
        ledger_module.effect_ledger._postgres = previous_ledger
        approval_store._tasks.clear()
        ledger_module.effect_ledger._records.clear()
        settings.database_url = original_url
        settings.checkpoint_backend = original_backend
        repo_module._pool = None


def _cleanup() -> None:
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("delete from approval_task where run_id = %s", (TURN_ID,))
        conn.execute("delete from effect_ledger where turn_id = %s", (TURN_ID,))
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
            try:
                conn.execute(
                    f"delete from agent_checkpoint.{table} where thread_id = %s",
                    (TURN_ID,),
                )
            except psycopg.errors.UndefinedTable:
                conn.rollback()
                break


@pytest.fixture(autouse=True)
def _clean_pg_state(pg_env, monkeypatch):
    async def fake_stream_completion(**_kwargs):
        yield ("answer", "已完成 VPN 开通。")

    monkeypatch.setattr(generate_node, "stream_completion", fake_stream_completion)


class CountingAdapter:
    def __init__(self) -> None:
        from app.tools.adapters.mock import execute_mock_tool

        self._execute = execute_mock_tool
        self.calls: list[str] = []

    async def execute(self, tool_name, args, state):
        self.calls.append(tool_name)
        return await self._execute(tool_name, args, state)


@pytest.fixture
def adapter(monkeypatch):
    counter = CountingAdapter()
    monkeypatch.setattr(service_workflow_node, "get_tool_adapter", lambda: counter)
    return counter


def make_request(turn_id: str):
    from app.api.schemas import (
        AgentStatePayload,
        ChatMessage,
        ChatRequest,
        UserContext,
    )

    roles = ["network_admin"]
    return ChatRequest(
        request_id=turn_id,
        user_context=UserContext(userId="u1", roles=roles),
        conversation_id="c1",
        user_message_id="m1",
        assistant_message_id="m2",
        messages=[ChatMessage(role="user", content="申请VPN权限")],
        latest_user_message="申请VPN权限",
        agent_state=AgentStatePayload(
            requestId=turn_id,
            userContext=UserContext(userId="u1", roles=roles),
            riskLevel="high",
        ),
    )


def _scalar(sql: str, params=()):
    with psycopg.connect(PG_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    return row[0] if row else None


def _rows(sql: str, params=()):
    with psycopg.connect(PG_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def test_interrupt_is_persisted_and_resumes_across_a_new_pool(adapter):
    """The whole acceptance path, against real PostgreSQL."""

    async def scenario():
        from app.core.effect_ledger import effect_key_for
        from app.graph.checkpoint import open_checkpointer
        from app.graph.root import build_root_graph
        from app.graph.runner import run_graph
        from app.services.approval_resume import resolve_approval

        events: list[dict] = []

        async def emit(event):
            events.append(event)

        # ---- first "process": raise the interrupt and persist the checkpoint
        async with open_checkpointer() as saver:
            await run_graph(make_request(TURN_ID), emit, graph=build_root_graph(saver))

        approvals = [e for e in events if e.get("type") == "approval_required"]
        assert len(approvals) == 1, events
        event = approvals[0]
        approval_id = event["approval_id"]

        assert event["checkpoint_id"], "the interrupt must expose a checkpoint id"
        assert event["interrupt_id"], "the interrupt must expose an interrupt id"

        # ---- the checkpoint really is in the database
        checkpoint_rows = _rows(
            "select checkpoint_ns, checkpoint_id from agent_checkpoint.checkpoints "
            "where thread_id = %s",
            (TURN_ID,),
        )
        assert checkpoint_rows, "no checkpoint row was persisted"
        namespaces = {row[0] for row in checkpoint_rows}
        assert event["checkpoint_ns"] in namespaces

        # ---- and so is the reference the approval row carries
        row = _rows(
            "select checkpoint_id, checkpoint_ns, interrupt_id, status, resume_state "
            "from approval_task where id = %s",
            (approval_id,),
        )
        assert row, "the approval row was not persisted"
        assert row[0][0] == event["checkpoint_id"]
        assert row[0][1] == event["checkpoint_ns"]
        assert row[0][2] == event["interrupt_id"]
        assert row[0][3] == "pending"
        assert row[0][4] == "queued"
        assert adapter.calls.count(WRITE_ACTION) == 0

        # ---- second "process": a brand new pool, same database
        async with open_checkpointer() as saver2:
            task = await approval_store.get(approval_id)
            assert task is not None
            assert task.checkpoint_id == event["checkpoint_id"]

            decision = await approval_store.decide(
                approval_id,
                "approved",
                resume_request_id="rsq_pg",
                user_id="approver1",
                actor_roles=["network_admin"],
            )
            assert decision.won is True

            resolution = await resolve_approval(
                decision.task, graph=build_root_graph(saver2), resume_request_id="rsq_pg"
            )

        assert resolution.status == "resumed", (resolution.status, resolution.error)
        assert adapter.calls.count(WRITE_ACTION) == 1

        # ---- exactly one successful external write was recorded
        effects = _rows(
            "select status, attempts from effect_ledger where effect_key = %s",
            (effect_key_for(TURN_ID, WRITE_ACTION),),
        )
        assert len(effects) == 1, effects
        assert effects[0][0] == "succeeded"
        assert effects[0][1] == 1

        # ---- the run finished, so the thread has no pending task left
        final = _rows(
            "select resume_state, consumed_at, status from approval_task where id = %s",
            (approval_id,),
        )
        assert final[0][0] == "succeeded"
        assert final[0][1] is not None
        assert final[0][2] == "approved"

        # ---- and a replay is idempotent
        reloaded = await approval_store.get(approval_id)
        replay = await resolve_approval(reloaded, graph=build_root_graph(saver2))
        assert replay.status == "already_resumed"
        assert adapter.calls.count(WRITE_ACTION) == 1

    _run(scenario)


def test_resume_is_refused_when_the_checkpoint_is_absent_from_postgres():
    async def scenario():
        from app.graph.checkpoint import open_checkpointer
        from app.graph.root import build_root_graph
        from app.graph.runner import TERMINAL_CHECKPOINT_ERRORS, resume_graph

        async with open_checkpointer() as saver:
            graph = build_root_graph(saver)
            outcome = await resume_graph(
                "turn_pg_missing",
                {
                    "decision": "approved",
                    "approval_id": "approval_missing",
                    "checkpoint_id": "00000000-0000-0000-0000-000000000000",
                    "checkpoint_ns": "",
                    "interrupt_id": "not-real",
                    "resume_request_id": "rsq_missing",
                },
                lambda _: asyncio.sleep(0),
                graph=graph,
            )

        assert outcome.status == "failed"
        assert outcome.error in TERMINAL_CHECKPOINT_ERRORS

        # No new run was started for that thread.
        assert (
            _scalar(
                "select count(*) from agent_checkpoint.checkpoints where thread_id = %s",
                ("turn_pg_missing",),
            )
            == 0
        )

    _run(scenario)


def test_approval_store_cas_is_single_winner_against_postgres():
    async def scenario():
        from app.persistence.approval_store import approval_key_for

        key = approval_key_for(TURN_ID, WRITE_ACTION)
        task = await approval_store.create(
            run_id=TURN_ID,
            turn_id=TURN_ID,
            thread_id=TURN_ID,
            user_id="u1",
            agent_id="network",
            intent=None,
            risk_level="high",
            rule_id="HIGH-RISK-APPROVAL-REQUIRED",
            approval_key=key,
            resume_payload={"pending_action": WRITE_ACTION},
        )

        # A real UNIQUE(approval_key) collision must return the existing row, not
        # raise or create a second approval.
        duplicate = await approval_store.create(
            run_id=TURN_ID,
            turn_id=TURN_ID,
            thread_id=TURN_ID,
            user_id="u1",
            agent_id="network",
            intent=None,
            risk_level="high",
            rule_id="HIGH-RISK-APPROVAL-REQUIRED",
            approval_key=key,
            resume_payload={"pending_action": WRITE_ACTION},
        )
        assert duplicate.id == task.id
        assert (
            _scalar(
                "select count(*) from approval_task where approval_key = %s", (key,)
            )
            == 1
        )

        results = await asyncio.gather(
            *[
                approval_store.decide(
                    task.id,
                    "approved",
                    resume_request_id=f"rsq_{index}",
                    user_id="approver1",
                    actor_roles=["network_admin"],
                )
                for index in range(8)
            ]
        )
        assert sum(1 for result in results if result.won) == 1
        assert (
            _scalar(
                "select count(*) from approval_task where run_id = %s and status = 'approved'",
                (TURN_ID,),
            )
            == 1
        )

    _run(scenario)
