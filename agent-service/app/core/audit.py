"""Append-only audit logging for administrative and security actions.

Deliberately separate from the business timeline: this table answers "who did
what to the platform and when", carries a retention policy, and does not record
day-to-day reads. Writing an audit record must never fail a user request, so
every persistence error is caught and logged while the event is still emitted to
the process log.
"""

import logging
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import settings
from app.persistence.postgres_repository import PostgresRepository

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    action: str
    actor_id: str | None = None
    actor_type: str = "user"
    object_type: str | None = None
    object_id: str | None = None
    outcome: str = "success"
    source_ip: str = ""
    user_agent: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    """Writes audit rows to PostgreSQL, with a bounded in-process mirror."""

    def __init__(self, postgres: Any = None, mirror_size: int = 500) -> None:
        self._postgres = postgres
        self._mirror: deque[dict[str, Any]] = deque(maxlen=mirror_size)

    @property
    def events(self) -> list[dict[str, Any]]:
        """Recent events, used by tests and by the log-only fallback."""
        return list(self._mirror)

    def _repository(self) -> Any:
        if self._postgres is not None:
            return self._postgres
        if not settings.database_url:
            return None
        self._postgres = PostgresRepository()
        return self._postgres

    async def record(self, event: AuditEvent) -> None:
        row = {
            "id": f"audit_{uuid.uuid4().hex[:24]}",
            "occurred_at": time.time(),
            **asdict(event),
        }
        self._mirror.append(row)

        if not settings.audit_enabled:
            return

        repository = self._repository()
        if repository is None:
            logger.info("AUDIT %s %s", event.action, row)
            return

        try:
            await repository.write_audit(row)
        except Exception:
            # Never let auditing break the request; the mirror and the process
            # log still carry the event.
            logger.exception("Failed to persist audit event %s", event.action)


audit_logger = AuditLogger()


async def record_action(
    action: str,
    *,
    caller: Any = None,
    object_type: str | None = None,
    object_id: str | None = None,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
    actor_id: str | None = None,
    actor_type: str = "user",
) -> None:
    """Convenience wrapper that accepts a `Caller` (or explicit actor fields)."""
    await audit_logger.record(
        AuditEvent(
            action=action,
            actor_id=actor_id if actor_id is not None else getattr(caller, "user_id", None),
            actor_type=getattr(caller, "actor_type", actor_type),
            object_type=object_type,
            object_id=object_id,
            outcome=outcome,
            source_ip=getattr(caller, "source_ip", "") or "",
            user_agent=getattr(caller, "user_agent", "") or "",
            detail=detail or {},
        )
    )
