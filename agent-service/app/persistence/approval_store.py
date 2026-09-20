import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.config import settings
from app.persistence.postgres_repository import PostgresRepository

ApprovalStatus = Literal["pending", "approved", "rejected"]


@dataclass
class ApprovalTask:
    id: str
    run_id: str
    user_id: str
    agent_id: str
    intent: str | None
    risk_level: str | None
    rule_id: str
    status: ApprovalStatus
    created_at: float
    updated_at: float
    resume_payload: dict[str, Any]
    result: dict[str, Any] | None


class ApprovalStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ApprovalTask] = {}
        self._postgres = PostgresRepository() if settings.database_url else None

    async def create(
        self,
        *,
        task_id: str,
        run_id: str,
        user_id: str,
        agent_id: str,
        intent: str | None,
        risk_level: str | None,
        rule_id: str,
        resume_payload: dict[str, Any] | None = None,
    ) -> ApprovalTask:
        now = time.time()
        task = ApprovalTask(
            id=task_id,
            run_id=run_id,
            user_id=user_id,
            agent_id=agent_id,
            intent=intent,
            risk_level=risk_level,
            rule_id=rule_id,
            status="pending",
            created_at=now,
            updated_at=now,
            resume_payload=resume_payload or {},
            result=None,
        )
        self._tasks[task.id] = task

        if self._postgres:
            try:
                await self._postgres.save_approval(asdict(task))
            except Exception:
                # The in-memory fallback keeps the demo flow usable when
                # PostgreSQL is unavailable.
                pass

        return task

    async def get(self, task_id: str) -> ApprovalTask | None:
        task = self._tasks.get(task_id)
        if task:
            return task

        if self._postgres:
            try:
                row = await self._postgres.get_approval(task_id)
                if row:
                    return self._from_row(row)
            except Exception:
                pass

        return None

    async def list_by_user(self, user_id: str) -> list[ApprovalTask]:
        if self._postgres:
            try:
                rows = await self._postgres.list_approvals(user_id)
                return [self._from_row(row) for row in rows]
            except Exception:
                pass

        return [
            task
            for task in self._tasks.values()
            if task.user_id == user_id
        ]

    async def decide(
        self,
        task_id: str,
        status: Literal["approved", "rejected"],
    ) -> ApprovalTask | None:
        task = self._tasks.get(task_id)
        if task:
            if task.status != "pending":
                return None
            task.status = status
            task.updated_at = time.time()

        if self._postgres:
            try:
                row = await self._postgres.decide_approval(task_id, status)
                if row:
                    persisted = self._from_row(row)
                    self._tasks[persisted.id] = persisted
                    return persisted
            except Exception:
                pass

        return task

    async def set_result(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> ApprovalTask | None:
        task = self._tasks.get(task_id)
        if task:
            task.result = result
            task.updated_at = time.time()

        if self._postgres:
            try:
                await self._postgres.save_approval(asdict(task or self._tasks[task_id]))
            except Exception:
                pass

        return task

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ApprovalTask:
        return ApprovalTask(
            id=row["id"],
            run_id=row["run_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            intent=row.get("intent"),
            risk_level=row.get("risk_level"),
            rule_id=row["rule_id"],
            status=row["status"],
            created_at=row["created_at"].timestamp()
            if hasattr(row["created_at"], "timestamp")
            else float(row["created_at"]),
            updated_at=row["updated_at"].timestamp()
            if hasattr(row["updated_at"], "timestamp")
            else float(row["updated_at"]),
            resume_payload=row.get("resume_payload") or {},
            result=row.get("result"),
        )


approval_store = ApprovalStore()
