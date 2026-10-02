import asyncio
import datetime as dt
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import psycopg
import psycopg.errors
import psycopg.rows

from app.config import settings
from app.runtime import require_async_postgres_supported

try:  # pragma: no cover - optional driver, required only in postgres mode
    from psycopg_pool import AsyncConnectionPool
except ModuleNotFoundError:  # pragma: no cover
    AsyncConnectionPool = None  # type: ignore[assignment]

_UNIQUE_VIOLATION: tuple[type[BaseException], ...] = (psycopg.errors.UniqueViolation,)


_pool: Any = None
_pool_lock = asyncio.Lock()


def _require_pool_driver() -> None:
    if AsyncConnectionPool is None:
        raise RuntimeError(
            "psycopg_pool is required for PostgreSQL persistence; install "
            "'psycopg-pool' or run with DATABASE_URL unset"
        )


async def get_pool() -> Any:
    """Lazily open one process-wide pool for business-table access."""
    global _pool
    _require_pool_driver()
    require_async_postgres_supported()
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                pool = AsyncConnectionPool(
                    conninfo=settings.database_url,
                    min_size=max(1, settings.checkpoint_pool_min_size),
                    max_size=settings.checkpoint_pool_max_size,
                    kwargs={
                        "autocommit": True,
                        "row_factory": psycopg.rows.dict_row,
                    },
                    open=False,
                )
                await pool.open()
                _pool = pool
    return _pool


async def close_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()


class PostgresRepository:
    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        pool = await get_pool()
        async with pool.connection() as conn:
            yield conn

    # ---------------------------------------------------------------- approvals

    async def create_approval(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """Insert one approval task, or return the existing row.

        The insert is deliberately conflict-tolerant: replaying the
        `prepare_approval` node must return the existing approval instead of
        creating a second one, and a unique `approval_key` collision (same turn,
        action and approval step) must resolve to that same row.
        """
        params = {
            "id": task["id"],
            "run_id": task["run_id"],
            "turn_id": task.get("turn_id") or task["run_id"],
            "thread_id": task.get("thread_id") or task.get("run_id"),
            "checkpoint_id": task.get("checkpoint_id"),
            "checkpoint_ns": task.get("checkpoint_ns"),
            "interrupt_id": task.get("interrupt_id"),
            "user_id": task["user_id"],
            "agent_id": task["agent_id"],
            "intent": task.get("intent"),
            "risk_level": task.get("risk_level"),
            "rule_id": task["rule_id"],
            "approval_key": task.get("approval_key"),
            "required_approver_roles": task.get("required_approver_roles"),
            "resume_request_id": task.get("resume_request_id"),
            "resume_state": task.get("resume_state") or "queued",
            "status": task["status"],
            "resume_payload": json.dumps(task.get("resume_payload", {}), ensure_ascii=False),
            "result": json.dumps(task.get("result"), ensure_ascii=False),
        }
        try:
            async with self._connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO approval_task (
                            id, run_id, turn_id, thread_id, checkpoint_id,
                            checkpoint_ns, interrupt_id, user_id, agent_id, intent,
                            risk_level, rule_id, approval_key, required_approver_roles,
                            resume_request_id, resume_state, status, resume_payload,
                            result, created_at, updated_at
                        ) VALUES (
                            %(id)s, %(run_id)s, %(turn_id)s, %(thread_id)s,
                            %(checkpoint_id)s, %(checkpoint_ns)s, %(interrupt_id)s,
                            %(user_id)s, %(agent_id)s, %(intent)s, %(risk_level)s,
                            %(rule_id)s, %(approval_key)s, %(required_approver_roles)s,
                            %(resume_request_id)s, %(resume_state)s, %(status)s,
                            %(resume_payload)s::jsonb, %(result)s::jsonb, NOW(), NOW()
                        )
                        ON CONFLICT (id) DO NOTHING
                        RETURNING *
                        """,
                        params,
                    )
                    row = await cur.fetchone()
        except _UNIQUE_VIOLATION:
            return await self.get_approval_by_key(task.get("approval_key"))

        if row:
            return dict(row)
        return await self.get_approval(task["id"])

    async def save_approval(self, task: dict[str, Any]) -> None:
        """Upsert the full mutable shape of an approval task."""
        async with self._connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO approval_task (
                        id, run_id, turn_id, thread_id, checkpoint_id,
                        checkpoint_ns, interrupt_id, user_id, agent_id, intent,
                        risk_level, rule_id, approval_key, required_approver_roles,
                        resume_request_id, resume_state, resume_attempts,
                        resume_error, consumed_at, decided_by, decided_at,
                        decision_comment, status, resume_payload, result,
                        created_at, updated_at
                    ) VALUES (
                        %(id)s, %(run_id)s, %(turn_id)s, %(thread_id)s,
                        %(checkpoint_id)s, %(checkpoint_ns)s, %(interrupt_id)s,
                        %(user_id)s, %(agent_id)s, %(intent)s, %(risk_level)s,
                        %(rule_id)s, %(approval_key)s, %(required_approver_roles)s,
                        %(resume_request_id)s, %(resume_state)s, %(resume_attempts)s,
                        %(resume_error)s, %(consumed_at)s, %(decided_by)s,
                        %(decided_at)s, %(decision_comment)s, %(status)s,
                        %(resume_payload)s::jsonb, %(result)s::jsonb, NOW(), NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        checkpoint_id = EXCLUDED.checkpoint_id,
                        checkpoint_ns = EXCLUDED.checkpoint_ns,
                        interrupt_id = EXCLUDED.interrupt_id,
                        approval_key = COALESCE(approval_task.approval_key, EXCLUDED.approval_key),
                        required_approver_roles = COALESCE(EXCLUDED.required_approver_roles, approval_task.required_approver_roles),
                        resume_request_id = COALESCE(EXCLUDED.resume_request_id, approval_task.resume_request_id),
                        resume_state = EXCLUDED.resume_state,
                        resume_attempts = EXCLUDED.resume_attempts,
                        resume_error = EXCLUDED.resume_error,
                        consumed_at = COALESCE(EXCLUDED.consumed_at, approval_task.consumed_at),
                        decided_by = COALESCE(EXCLUDED.decided_by, approval_task.decided_by),
                        decided_at = COALESCE(EXCLUDED.decided_at, approval_task.decided_at),
                        decision_comment = COALESCE(EXCLUDED.decision_comment, approval_task.decision_comment),
                        status = EXCLUDED.status,
                        result = EXCLUDED.result,
                        updated_at = NOW()
                    """,
                    {
                        "id": task["id"],
                        "run_id": task["run_id"],
                        "turn_id": task.get("turn_id") or task["run_id"],
                        "thread_id": task.get("thread_id") or task.get("run_id"),
                        "checkpoint_id": task.get("checkpoint_id"),
                        "checkpoint_ns": task.get("checkpoint_ns"),
                        "interrupt_id": task.get("interrupt_id"),
                        "user_id": task["user_id"],
                        "agent_id": task["agent_id"],
                        "intent": task.get("intent"),
                        "risk_level": task.get("risk_level"),
                        "rule_id": task["rule_id"],
                        "approval_key": task.get("approval_key"),
                        "required_approver_roles": task.get("required_approver_roles"),
                        "resume_request_id": task.get("resume_request_id"),
                        "resume_state": task.get("resume_state") or "queued",
                        "resume_attempts": int(task.get("resume_attempts") or 0),
                        "resume_error": task.get("resume_error"),
                        "consumed_at": _to_datetime(task.get("consumed_at")),
                        "decided_by": task.get("decided_by"),
                        "decided_at": _to_datetime(task.get("decided_at")),
                        "decision_comment": task.get("decision_comment"),
                        "status": task["status"],
                        "resume_payload": json.dumps(
                            task.get("resume_payload", {}),
                            ensure_ascii=False,
                        ),
                        "result": json.dumps(
                            task.get("result"),
                            ensure_ascii=False,
                        ),
                    },
                )

    # ------------------------------------------------------------------- audit

    async def write_audit(self, row: dict[str, Any]) -> None:
        async with self._connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO audit_log (
                        id, occurred_at, actor_id, actor_type, action,
                        object_type, object_id, outcome, source_ip, user_agent, detail
                    ) VALUES (
                        %(id)s, NOW(), %(actor_id)s, %(actor_type)s, %(action)s,
                        %(object_type)s, %(object_id)s, %(outcome)s, %(source_ip)s,
                        %(user_agent)s, %(detail)s::jsonb
                    )
                    """,
                    {
                        "id": row["id"],
                        "actor_id": row.get("actor_id"),
                        "actor_type": row.get("actor_type") or "user",
                        "action": row["action"],
                        "object_type": row.get("object_type"),
                        "object_id": row.get("object_id"),
                        "outcome": row.get("outcome") or "success",
                        "source_ip": row.get("source_ip") or None,
                        "user_agent": row.get("user_agent") or None,
                        "detail": json.dumps(
                            row.get("detail") or {}, ensure_ascii=False, default=str
                        ),
                    },
                )

    # --------------------------------------------------------------- approvals

    async def record_approval_decision(
        self,
        *,
        approval_id: str,
        decision: str,
        decided_by: str,
        decided_by_roles: str = "",
        comment: str | None = None,
        source_ip: str | None = None,
        resume_request_id: str | None = None,
    ) -> None:
        async with self._connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO approval_decision (
                        id, approval_id, decision, decided_by, decided_by_roles,
                        comment, source_ip, resume_request_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        f"decision_{uuid.uuid4().hex[:24]}",
                        approval_id,
                        decision,
                        decided_by,
                        decided_by_roles,
                        comment,
                        source_ip,
                        resume_request_id,
                    ),
                )

    async def update_approval_checkpoint_refs(
        self,
        task_id: str,
        *,
        checkpoint_id: str,
        checkpoint_ns: str,
        interrupt_id: str,
    ) -> dict[str, Any] | None:
        async with self._connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE approval_task
                    SET checkpoint_id = %s,
                        checkpoint_ns = %s,
                        interrupt_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (checkpoint_id, checkpoint_ns, interrupt_id, task_id),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_approval(self, task_id: str) -> dict[str, Any] | None:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM approval_task WHERE id = %s",
                    (task_id,),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_approval_by_key(self, approval_key: str | None) -> dict[str, Any] | None:
        if not approval_key:
            return None
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM approval_task WHERE approval_key = %s LIMIT 1",
                    (approval_key,),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_approvals(self, user_id: str) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    SELECT * FROM approval_task
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                )
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def decide_approval(
        self,
        task_id: str,
        status: str,
        *,
        resume_request_id: str | None = None,
        decided_by: str | None = None,
        decision_comment: str | None = None,
    ) -> dict[str, Any] | None:
        """Compare-and-swap `pending` -> `approved`/`rejected`.

        Exactly one caller can win this update, and the winning caller is the one
        that owns the resume request id. Returning no row means somebody else
        already decided, so the caller must not start a resume.

        The update is deliberately **not** scoped to `approval_task.user_id`: that
        column is the *requester*, and separation of duties requires the decision
        to come from someone else. Authorization happens before the CAS (see
        `ApprovalStore.decide`); the CAS only guarantees a single winner. The
        decider's identity and comment are written in the same statement so an
        approval can never end up decided without attribution.
        """
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE approval_task
                    SET status = %s,
                        resume_request_id = %s,
                        resume_state = 'queued',
                        resume_attempts = 0,
                        resume_error = NULL,
                        decided_by = COALESCE(%s, decided_by),
                        decided_at = NOW(),
                        decision_comment = COALESCE(%s, decision_comment),
                        updated_at = NOW()
                    WHERE id = %s
                      AND status = 'pending'
                    RETURNING *
                    """,
                    (
                        status,
                        resume_request_id,
                        decided_by,
                        decision_comment,
                        task_id,
                    ),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_approval_decisions(self, approval_id: str) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, approval_id, decision, decided_by, decided_by_roles,
                           comment, source_ip, resume_request_id, created_at
                    FROM approval_decision
                    WHERE approval_id = %s
                    ORDER BY created_at ASC
                    """,
                    (approval_id,),
                )
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def claim_resume(
        self,
        task_id: str,
        *,
        resume_request_id: str | None = None,
        stale_running_seconds: int = 120,
    ) -> dict[str, Any] | None:
        """Atomically take ownership of one resume attempt.

        `queued`/`failed` rows are claimable immediately. A `running` row is only
        claimable after `stale_running_seconds`, which is how a worker that died
        mid-resume releases its claim to the next reconciler tick.
        """
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE approval_task
                    SET resume_state = 'running',
                        resume_request_id = COALESCE(resume_request_id, %s),
                        resume_attempts = resume_attempts + 1,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status IN ('approved', 'rejected')
                      AND (
                          resume_state IN ('queued', 'failed')
                          OR (
                              resume_state = 'running'
                              AND updated_at < NOW() - (%s * INTERVAL '1 second')
                          )
                      )
                    RETURNING *
                    """,
                    (resume_request_id, task_id, stale_running_seconds),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_resume_result(
        self,
        task_id: str,
        *,
        resume_state: str,
        resume_error: str | None = None,
        consumed: bool = False,
    ) -> dict[str, Any] | None:
        """Record how a resume attempt ended.

        The `resume_state <> 'succeeded'` guard is deliberate: a duplicate
        delivery that arrives after a successful one reads an already-consumed
        checkpoint and would otherwise report STALE_APPROVAL, overwriting the
        successful result. Success is terminal.
        """
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE approval_task
                    SET resume_state = %s,
                        resume_error = %s,
                        consumed_at = CASE WHEN %s THEN NOW() ELSE consumed_at END,
                        updated_at = NOW()
                    WHERE id = %s
                      AND resume_state <> 'succeeded'
                    RETURNING *
                    """,
                    (resume_state, resume_error, consumed, task_id),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_resume_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    SELECT * FROM approval_task
                    WHERE status IN ('approved', 'rejected')
                      AND resume_attempts < %s
                      AND (checkpoint_id IS NOT NULL OR interrupt_id IS NOT NULL)
                      AND (
                          resume_state IN ('queued', 'failed')
                          OR (
                              resume_state = 'running'
                              AND updated_at < NOW() - (%s * INTERVAL '1 second')
                          )
                      )
                    ORDER BY updated_at ASC
                    LIMIT %s
                    """,
                    (
                        settings.resume_max_attempts,
                        max(30, settings.resume_claim_timeout_seconds),
                        limit,
                    ),
                )
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ effect ledger

    async def begin_effect(
        self,
        effect_key: str,
        *,
        turn_id: str,
        approval_id: str | None,
        action: str,
        provider_key: str | None,
    ) -> dict[str, Any]:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO effect_ledger (
                        effect_key, turn_id, approval_id, action, status,
                        provider_key, attempts, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 'started', %s, 1, NOW(), NOW())
                    ON CONFLICT (effect_key) DO UPDATE SET
                        attempts = effect_ledger.attempts + 1,
                        provider_key = COALESCE(EXCLUDED.provider_key, effect_ledger.provider_key),
                        updated_at = NOW()
                    RETURNING *
                    """,
                    (effect_key, turn_id, approval_id, action, provider_key),
                )
                row = await cur.fetchone()
        return dict(row)

    async def get_effect(self, effect_key: str) -> dict[str, Any] | None:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM effect_ledger WHERE effect_key = %s",
                    (effect_key,),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def finish_effect(
        self,
        effect_key: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with self._connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE effect_ledger
                    SET status = %s,
                        response = %s::jsonb,
                        error = %s,
                        updated_at = NOW()
                    WHERE effect_key = %s
                    """,
                    (
                        status,
                        json.dumps(response, ensure_ascii=False, default=str),
                        error,
                        effect_key,
                    ),
                )

    # ---------------------------------------------------------------- knowledge

    async def search_knowledge(
        self,
        query: str,
        roles: list[str],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, title, content, allowed_roles
                    FROM knowledge_document
                    WHERE content ILIKE %s
                      AND (
                          allowed_roles = '[]'::jsonb
                          OR allowed_roles ?| array[%s]
                      )
                    ORDER BY title
                    LIMIT %s
                    """,
                    (f"%{query}%", roles, limit),
                )
                rows = await cur.fetchall()
        return [dict(row) for row in rows]


def _to_datetime(value: Any):
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return value
    return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
