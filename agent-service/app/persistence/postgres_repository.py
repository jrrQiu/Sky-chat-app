import json
from typing import Any

import psycopg
import psycopg.rows

from app.config import settings


class PostgresRepository:
    async def _connect(self):
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        return await psycopg.AsyncConnection.connect(settings.database_url)

    async def save_approval(self, task: dict[str, Any]) -> None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO approval_task (
                        id, run_id, user_id, agent_id, intent, risk_level,
                        rule_id, status, resume_payload, result, created_at, updated_at
                    ) VALUES (
                        %(id)s, %(run_id)s, %(user_id)s, %(agent_id)s, %(intent)s,
                        %(risk_level)s, %(rule_id)s, %(status)s,
                        %(resume_payload)s::jsonb, %(result)s::jsonb, NOW(), NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        result = EXCLUDED.result,
                        updated_at = NOW()
                    """,
                    {
                        "id": task["id"],
                        "run_id": task["run_id"],
                        "user_id": task["user_id"],
                        "agent_id": task["agent_id"],
                        "intent": task.get("intent"),
                        "risk_level": task.get("risk_level"),
                        "rule_id": task["rule_id"],
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

    async def get_approval(self, task_id: str) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM approval_task WHERE id = %s",
                    (task_id,),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_approvals(self, user_id: str) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
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
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE approval_task
                    SET status = %s, updated_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    RETURNING *
                    """,
                    (status, task_id),
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def search_knowledge(
        self,
        query: str,
        roles: list[str],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
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
