import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.config import settings
from app.persistence.postgres_repository import PostgresRepository

logger = logging.getLogger(__name__)

ApprovalStatus = Literal["pending", "approved", "rejected"]
ResumeState = Literal["queued", "running", "succeeded", "failed", "stale"]

_UNSET: Any = object()


class ApprovalStoreUnavailable(RuntimeError):
    """Raised when the durable approval store cannot be reached.

    Losing the approval row means losing the checkpoint reference that a resume
    depends on, so the caller must fail closed (HTTP 503) instead of guessing.
    """


class ApprovalPolicyError(RuntimeError):
    """A decision was refused by an approval policy, not by a technical fault.

    `code` is a machine-readable reason the HTTP layer maps to 403:
    `SELF_APPROVAL_FORBIDDEN`, `APPROVER_ROLE_REQUIRED`, `APPROVAL_NOT_PENDING`.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def approval_key_for(turn_id: str, action: str, step: str = "approval") -> str:
    return hashlib.sha256(f"{turn_id}:{action}:{step}".encode("utf-8")).hexdigest()


def approval_id_for(approval_key: str) -> str:
    return f"approval_{approval_key[:24]}"


@dataclass
class ApprovalTask:
    id: str
    run_id: str
    user_id: str
    agent_id: str
    intent: str | None
    risk_level: str | None
    rule_id: str
    approval_key: str
    status: ApprovalStatus
    created_at: float
    updated_at: float
    required_approver_roles: str = ""
    resume_payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    turn_id: str = ""
    thread_id: str = ""
    checkpoint_id: str | None = None
    checkpoint_ns: str = ""
    interrupt_id: str | None = None
    resume_request_id: str | None = None
    resume_state: str = "queued"
    resume_attempts: int = 0
    resume_error: str | None = None
    consumed_at: float | None = None
    decided_by: str | None = None
    decided_at: float | None = None
    decision_comment: str | None = None


@dataclass
class DecisionResult:
    task: ApprovalTask | None
    won: bool
    reason: Literal["decided", "already_decided", "not_found"]


class ApprovalStore:
    def __init__(self, postgres: Any = _UNSET) -> None:
        self._tasks: dict[str, ApprovalTask] = {}
        self._decisions: list[dict[str, Any]] = []
        if postgres is _UNSET:
            postgres = PostgresRepository() if settings.database_url else None
        self._postgres = postgres

    # ----------------------------------------------------------------- internals

    @property
    def durable(self) -> bool:
        return self._postgres is not None

    async def _call(self, operation: str, awaitable):
        try:
            return await awaitable
        except Exception as exc:
            logger.exception("Approval store operation failed: %s", operation)
            raise ApprovalStoreUnavailable(f"{operation} failed") from exc

    # -------------------------------------------------------------------- writes

    async def create(
        self,
        *,
        task_id: str | None = None,
        run_id: str,
        user_id: str,
        agent_id: str,
        intent: str | None,
        risk_level: str | None,
        rule_id: str,
        approval_key: str | None = None,
        resume_payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
        thread_id: str | None = None,
        required_approver_roles: str = "",
    ) -> ApprovalTask:
        """Create the approval for one turn + action, or return the existing one.

        Defensive idempotency: the approval key is unique, so replaying the
        `prepare_approval` node (or a duplicated request) returns the first row
        instead of creating a second approval for the same write.
        """
        action = str(
            (resume_payload or {}).get("pending_action") or "approval_decision"
        )
        resolved_turn_id = turn_id or run_id
        key = approval_key or approval_key_for(resolved_turn_id, action)
        resolved_id = task_id or approval_id_for(key)
        now = time.time()

        task = ApprovalTask(
            id=resolved_id,
            run_id=run_id,
            user_id=user_id,
            agent_id=agent_id,
            intent=intent,
            risk_level=risk_level,
            rule_id=rule_id,
            approval_key=key,
            status="pending",
            created_at=now,
            updated_at=now,
            resume_payload=resume_payload or {},
            turn_id=resolved_turn_id,
            thread_id=thread_id or resolved_turn_id,
            required_approver_roles=required_approver_roles,
        )

        if self._postgres is None:
            existing = self._tasks.get(resolved_id)
            if existing:
                return existing
            for candidate in self._tasks.values():
                if candidate.approval_key == key:
                    return candidate
            self._tasks[task.id] = task
            return task

        row = await self._call(
            "create approval",
            self._postgres.create_approval(asdict(task)),
        )
        if row is None:
            raise ApprovalStoreUnavailable("create approval returned no row")
        return self._from_row(row)

    async def update_checkpoint_refs(
        self,
        task_id: str,
        *,
        checkpoint_id: str,
        checkpoint_ns: str,
        interrupt_id: str,
    ) -> ApprovalTask | None:
        """Persist the LangGraph checkpoint reference in the approval row.

        Only the agent may write these fields: they are read back from the graph
        snapshot, never submitted by a client.
        """
        if self._postgres is None:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.checkpoint_id = checkpoint_id
            task.checkpoint_ns = checkpoint_ns
            task.interrupt_id = interrupt_id
            task.updated_at = time.time()
            return task

        row = await self._call(
            "update checkpoint refs",
            self._postgres.update_approval_checkpoint_refs(
                task_id,
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
                interrupt_id=interrupt_id,
            ),
        )
        return self._from_row(row) if row else None

    async def decide(
        self,
        task_id: str,
        status: Literal["approved", "rejected"],
        *,
        resume_request_id: str | None = None,
        user_id: str | None = None,
        actor_roles: list[str] | None = None,
        comment: str | None = None,
        source_ip: str | None = None,
        enforce_policy: bool = True,
    ) -> DecisionResult:
        """Compare-and-swap one pending approval into its final status.

        Policies are enforced here rather than in the HTTP layer so that every
        caller — the public endpoint, the internal endpoint and the reconciler —
        is bound by them:

        * **separation of duties**: the requester can never approve their own
          request;
        * **required approver roles**: when the rule names approvers, the decider
          must hold one of those roles (`admin` always passes).
        """
        task = await self.get(task_id)
        if task is None:
            return DecisionResult(None, won=False, reason="not_found")

        if enforce_policy:
            if user_id and user_id == task.user_id:
                raise ApprovalPolicyError(
                    "SELF_APPROVAL_FORBIDDEN",
                    "the requester cannot decide their own approval",
                )
            required = [
                role.strip()
                for role in (task.required_approver_roles or "").split(",")
                if role.strip()
            ]
            if required:
                roles = {role.strip() for role in (actor_roles or [])}
                if "admin" not in roles and not roles.intersection(required):
                    raise ApprovalPolicyError(
                        "APPROVER_ROLE_REQUIRED",
                        f"one of {required} is required to decide this approval",
                    )

        if self._postgres is None:
            if task.status != "pending":
                return DecisionResult(task, won=False, reason="already_decided")
            task.status = status
            task.resume_request_id = resume_request_id or task.resume_request_id
            task.resume_state = "queued"
            task.resume_attempts = 0
            task.resume_error = None
            task.decided_by = task.decided_by or user_id
            task.decided_at = task.decided_at or time.time()
            task.decision_comment = comment or task.decision_comment
            task.updated_at = time.time()
            await self._record_decision_trail(
                task,
                status,
                user_id=user_id,
                actor_roles=actor_roles,
                comment=comment,
                source_ip=source_ip,
            )
            return DecisionResult(task, won=True, reason="decided")

        row = await self._call(
            "decide approval",
            self._postgres.decide_approval(
                task_id,
                status,
                resume_request_id=resume_request_id,
                decided_by=user_id,
                decision_comment=comment,
            ),
        )
        if row:
            decided = self._from_row(row)
            # Append-only trail: how the approval reached this state.
            await self._record_decision_trail(
                decided,
                status,
                user_id=user_id,
                actor_roles=actor_roles,
                comment=comment,
                source_ip=source_ip,
            )
            return DecisionResult(decided, won=True, reason="decided")

        existing = await self.get(task_id)
        if existing is None:
            return DecisionResult(None, won=False, reason="not_found")
        return DecisionResult(existing, won=False, reason="already_decided")

    async def _record_decision_trail(
        self,
        task: ApprovalTask,
        status: str,
        *,
        user_id: str | None,
        actor_roles: list[str] | None,
        comment: str | None,
        source_ip: str | None,
    ) -> None:
        if self._postgres is None:
            self._decisions.append(
                {
                    "id": f"decision_{len(self._decisions) + 1}",
                    "approval_id": task.id,
                    "decision": status,
                    "decided_by": str(user_id or "unknown"),
                    "decided_by_roles": ",".join(actor_roles or []),
                    "comment": comment,
                    "source_ip": source_ip,
                    "resume_request_id": task.resume_request_id,
                    "created_at": time.time(),
                }
            )
            return
        try:
            await self._postgres.record_approval_decision(
                approval_id=task.id,
                decision=status,
                decided_by=str(user_id or "unknown"),
                decided_by_roles=",".join(actor_roles or []),
                comment=comment,
                source_ip=source_ip,
                resume_request_id=task.resume_request_id,
            )
        except Exception:
            # The decision is already committed; a missing trail row is a
            # monitoring issue, not a reason to fail the request.
            logger.exception("Failed to append the decision trail for %s", task.id)

    async def list_decisions(self, task_id: str) -> list[dict[str, Any]]:
        if self._postgres is None:
            return [
                dict(row) for row in self._decisions if row["approval_id"] == task_id
            ]
        return await self._call(
            "list approval decisions",
            self._postgres.list_approval_decisions(task_id),
        )

    async def claim_resume(
        self,
        task_id: str,
        *,
        resume_request_id: str | None = None,
        stale_running_seconds: int | None = None,
    ) -> ApprovalTask | None:
        """Atomically take ownership of one resume attempt.

        Returns `None` when another worker already owns the attempt or the work
        is done, which is what makes running a reconciler on every replica safe.
        """
        stale_after = (
            settings.resume_claim_timeout_seconds
            if stale_running_seconds is None
            else stale_running_seconds
        )
        if self._postgres is None:
            task = self._tasks.get(task_id)
            if task is None or task.status not in {"approved", "rejected"}:
                return None
            if task.resume_state not in {"queued", "failed", "running"}:
                return None
            if task.resume_state == "running":
                age = time.time() - task.updated_at
                if age < stale_after:
                    return None
            task.resume_state = "running"
            task.resume_request_id = task.resume_request_id or resume_request_id
            task.resume_attempts += 1
            task.updated_at = time.time()
            return task

        row = await self._call(
            "claim resume",
            self._postgres.claim_resume(
                task_id,
                resume_request_id=resume_request_id,
                stale_running_seconds=stale_after,
            ),
        )
        return self._from_row(row) if row else None

    async def mark_resume_result(
        self,
        task_id: str,
        *,
        resume_state: ResumeState,
        resume_error: str | None = None,
        consumed: bool = False,
    ) -> ApprovalTask | None:
        """Record how a resume attempt ended. `succeeded` is terminal.

        A duplicate delivery that loses the race reads an already-consumed
        checkpoint and would report STALE_APPROVAL; without this guard that
        would overwrite the successful result.
        """
        if self._postgres is None:
            task = self._tasks.get(task_id)
            if not task or task.resume_state == "succeeded":
                return None
            task.resume_state = resume_state
            task.resume_error = resume_error
            if consumed:
                task.consumed_at = time.time()
            task.updated_at = time.time()
            return task

        row = await self._call(
            "mark resume result",
            self._postgres.mark_resume_result(
                task_id,
                resume_state=resume_state,
                resume_error=resume_error,
                consumed=consumed,
            ),
        )
        return self._from_row(row) if row else None

    async def set_result(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> ApprovalTask | None:
        task = await self.get(task_id)
        if task is None:
            return None
        task.result = result
        task.updated_at = time.time()
        if self._postgres is not None:
            await self._call(
                "save approval result",
                self._postgres.save_approval(asdict(task)),
            )
        else:
            self._tasks[task.id] = task
        return task

    # --------------------------------------------------------------------- reads

    async def get(self, task_id: str) -> ApprovalTask | None:
        if self._postgres is None:
            return self._tasks.get(task_id)
        row = await self._call("get approval", self._postgres.get_approval(task_id))
        return self._from_row(row) if row else None

    async def get_by_key(self, approval_key: str) -> ApprovalTask | None:
        if self._postgres is None:
            for task in self._tasks.values():
                if task.approval_key == approval_key:
                    return task
            return None
        row = await self._call(
            "get approval by key",
            self._postgres.get_approval_by_key(approval_key),
        )
        return self._from_row(row) if row else None

    async def list_by_user(self, user_id: str) -> list[ApprovalTask]:
        if self._postgres is None:
            return [task for task in self._tasks.values() if task.user_id == user_id]
        rows = await self._call(
            "list approvals",
            self._postgres.list_approvals(user_id),
        )
        return [self._from_row(row) for row in rows]

    async def list_resume_candidates(self, limit: int = 20) -> list[ApprovalTask]:
        if self._postgres is None:
            now = time.time()
            candidates = []
            for task in self._tasks.values():
                if task.status not in {"approved", "rejected"}:
                    continue
                if task.resume_attempts >= settings.resume_max_attempts:
                    continue
                if not (task.checkpoint_id or task.interrupt_id):
                    continue
                if task.resume_state in {"queued", "failed"}:
                    candidates.append(task)
                elif (
                    task.resume_state == "running"
                    and now - task.updated_at >= settings.resume_claim_timeout_seconds
                ):
                    candidates.append(task)
            candidates.sort(key=lambda item: item.updated_at)
            return candidates[:limit]
        rows = await self._call(
            "list resume candidates",
            self._postgres.list_resume_candidates(limit),
        )
        return [self._from_row(row) for row in rows]

    # ------------------------------------------------------------------- mapping

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ApprovalTask:
        run_id = str(row["run_id"])
        payload = row.get("resume_payload") or {}
        action = str(payload.get("pending_action") or "approval_decision")
        return ApprovalTask(
            id=row["id"],
            run_id=run_id,
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            intent=row.get("intent"),
            risk_level=row.get("risk_level"),
            rule_id=row["rule_id"],
            approval_key=row.get("approval_key")
            or approval_key_for(run_id, action),
            status=row["status"],
            created_at=_to_epoch(row.get("created_at")),
            updated_at=_to_epoch(row.get("updated_at")),
            required_approver_roles=row.get("required_approver_roles") or "",
            resume_payload=payload,
            result=row.get("result"),
            turn_id=str(row.get("turn_id") or run_id),
            thread_id=str(row.get("thread_id") or row.get("turn_id") or run_id),
            checkpoint_id=row.get("checkpoint_id"),
            checkpoint_ns=row.get("checkpoint_ns") or "",
            interrupt_id=row.get("interrupt_id"),
            resume_request_id=row.get("resume_request_id"),
            resume_state=row.get("resume_state") or "queued",
            resume_attempts=int(row.get("resume_attempts") or 0),
            resume_error=row.get("resume_error"),
            consumed_at=_to_epoch(row.get("consumed_at")) if row.get("consumed_at") else None,
            decided_by=row.get("decided_by"),
            decided_at=_to_epoch(row.get("decided_at")) if row.get("decided_at") else None,
            decision_comment=row.get("decision_comment"),
        )


def _to_epoch(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    return float(value)


approval_store = ApprovalStore()
