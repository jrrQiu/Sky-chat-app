import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.config import settings
from app.persistence.postgres_repository import PostgresRepository

logger = logging.getLogger(__name__)

_UNSET: Any = object()


def effect_key_for(turn_id: str, action: str) -> str:
    """Stable execution key for one turn + one approved action."""
    return hashlib.sha256(f"{turn_id}:{action}".encode("utf-8")).hexdigest()


@dataclass
class EffectRecord:
    effect_key: str
    turn_id: str
    approval_id: str | None
    action: str
    status: str
    provider_key: str | None
    attempts: int
    response: dict[str, Any] | None


class EffectLedger:
    """At-most-once guard for external write tools.

    LangGraph re-enters the interrupted node when a run resumes, and a process
    crash mid-node is replayed from the previous checkpoint. Both cases can call
    the write tool again, so every write is wrapped in a ledger row keyed by
    `turn_id + action`. A `succeeded` row short-circuits the call; the same key
    is also forwarded to the provider so a real system can deduplicate too.
    """

    def __init__(self, postgres: Any = _UNSET) -> None:
        self._records: dict[str, EffectRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        if postgres is _UNSET:
            postgres = PostgresRepository() if settings.database_url else None
        self._postgres = postgres

    def _lock(self, effect_key: str) -> asyncio.Lock:
        lock = self._locks.get(effect_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[effect_key] = lock
        return lock

    async def _lookup(self, effect_key: str) -> EffectRecord | None:
        local = self._records.get(effect_key)
        if local is not None:
            return local
        if self._postgres is None:
            return None
        try:
            row = await self._postgres.get_effect(effect_key)
        except Exception:
            self._raise_or_log("read effect ledger", effect_key)
            return None
        if not row:
            return None
        record = EffectRecord(
            effect_key=row["effect_key"],
            turn_id=row["turn_id"],
            approval_id=row.get("approval_id"),
            action=row["action"],
            status=row["status"],
            provider_key=row.get("provider_key"),
            attempts=int(row.get("attempts") or 0),
            response=row.get("response"),
        )
        self._records[effect_key] = record
        return record

    async def _raise_or_log(self, operation: str, effect_key: str) -> None:
        if settings.durable_persistence:
            raise RuntimeError(f"Failed to {operation} for {effect_key}")
        logger.warning("Failed to %s for %s; using in-process state", operation, effect_key)

    async def run_once(
        self,
        *,
        turn_id: str,
        action: str,
        approval_id: str | None,
        execute: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Execute `execute(provider_key)` at most once per turn + action."""
        effect_key = effect_key_for(turn_id, action)
        provider_key = f"idem_{effect_key[:40]}"

        async with self._lock(effect_key):
            existing = await self._lookup(effect_key)
            if existing and existing.status == "succeeded" and existing.response:
                logger.info("Effect %s already succeeded; skipping write", effect_key)
                return {**existing.response, "deduplicated": True}

            record = EffectRecord(
                effect_key=effect_key,
                turn_id=turn_id,
                approval_id=approval_id,
                action=action,
                status="started",
                provider_key=provider_key,
                attempts=(existing.attempts + 1) if existing else 1,
                response=None,
            )
            self._records[effect_key] = record

            if self._postgres is not None:
                try:
                    await self._postgres.begin_effect(
                        effect_key,
                        turn_id=turn_id,
                        approval_id=approval_id,
                        action=action,
                        provider_key=provider_key,
                    )
                except Exception:
                    await self._raise_or_log("begin effect", effect_key)

            try:
                result = await execute(provider_key)
            except Exception as exc:
                record.status = "failed"
                if self._postgres is not None:
                    try:
                        await self._postgres.finish_effect(
                            effect_key,
                            status="failed",
                            error=str(exc),
                        )
                    except Exception:
                        await self._raise_or_log("finish failed effect", effect_key)
                raise

            succeeded = bool(result.get("success", True))
            record.status = "succeeded" if succeeded else "failed"
            record.response = result

            if self._postgres is not None:
                try:
                    await self._postgres.finish_effect(
                        effect_key,
                        status=record.status,
                        response=result,
                        error=None if succeeded else str(result.get("text", "")),
                    )
                except Exception:
                    await self._raise_or_log("finish effect", effect_key)

            return result


effect_ledger = EffectLedger()
