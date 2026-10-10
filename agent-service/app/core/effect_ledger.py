"""The effect ledger: at-most-once for external writes, including across a crash.

Every write tool is wrapped in a row keyed by ``turn_id + action``. A ``succeeded`` row
short-circuits the call, which covers the common replay (LangGraph re-enters an
interrupted node, or a node is re-run after a checkpoint) without any extra machinery.

The case that used to be unprotected is a **crash between the row being written as
``started`` and the row being finished**. On replay the old code simply ran the effect
again, because only ``succeeded`` short-circuited. With ``ticket_create`` and
``ticket_comment`` no longer requiring an approval, that meant a crash could create a
second ticket or post a second public comment in the user's name.

The fix is a lease, and it is deliberately narrow:

* a ``started`` row whose lease is **still live** means somebody is working on it right
  now. The replay does not run the effect and does not claim success either -- it returns
  ``EFFECT_IN_PROGRESS``, which is a failure the model can explain ("上一次相同操作仍在进行
  中") rather than an exception.
* a ``started`` row whose lease has **expired** is a crashed attempt. Exactly one caller
  may take it over, and it does so with a compare-and-swap on the row it read, so two
  concurrent replays cannot both win.
* a ``failed`` row may be retried: the previous attempt is known not to have produced the
  effect (the tool reported failure), and refusing to retry would make a transient failure
  permanent.

The lease duration is derived from the tool timeout rather than hard-coded, so a
legitimately slow call is not mistaken for a crash. The lease itself is inferred from
``updated_at``, which the table already has, so no migration is needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.config import settings
from app.observability.registry import metrics
from app.persistence.postgres_repository import PostgresRepository

logger = logging.getLogger(__name__)

METRIC_LEASE_CONFLICTS = "effect_ledger.lease_conflicts"
METRIC_LEASE_TAKEOVERS = "effect_ledger.lease_takeovers"
HELP_LEASE_CONFLICTS = (
    "Write attempts refused because an identical effect was still in flight."
)
HELP_LEASE_TAKEOVERS = (
    "Write attempts that took over a lease left behind by a crashed attempt."
)

#: Error code returned to the caller when a live lease blocks the effect. The model treats
#: this as a normal, explainable failure.
EFFECT_IN_PROGRESS = "EFFECT_IN_PROGRESS"

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
    #: When the row was last touched, used to derive lease liveness.
    updated_at: float | None = None


@dataclass(frozen=True)
class Claim:
    """The answer to "may this effect run?"."""

    run: bool
    record: EffectRecord | None = None
    reason: str = ""
    #: True when a crashed attempt's lease was taken over (recorded so it is visible).
    takeover: bool = False


def _worst_single_call_seconds() -> float:
    """The longest one tool call can legitimately run, from the caps that bound it.

    Two independent caps apply: the hard per-call deadline, and the retry policy's own worst
    case (attempts x timeout + the wait budget). Taking the smaller of the two is what the
    caller can actually observe, and it is the number the lease has to clear.
    """
    retry_worst = (
        float(settings.tool_retry_read_attempts) * float(settings.tool_timeout_seconds)
        + float(settings.tool_retry_total_budget_seconds)
    )
    return max(1.0, min(float(settings.tool_call_deadline_seconds), retry_worst))


def lease_seconds() -> float:
    """How long a ``started`` row is trusted, floored at 60s.

    The lease must be **longer than the worst case for a single call**, otherwise a write that
    is still executing would have its lease taken over by a replay and be applied twice. That
    is why the floor is derived from the call caps rather than from a single timeout constant:
    the first version keyed off ``tool_timeout_seconds`` alone, which stopped describing the
    worst case as soon as a hard per-call deadline and a retry policy were added.
    """
    if not settings.effect_lease_enabled:
        return 0.0
    configured = float(settings.effect_lease_seconds or 0.0)
    derived = max(60.0, _worst_single_call_seconds() * 2.0)
    return max(configured, derived) if configured else derived


def _updated_at_epoch(row: dict[str, Any]) -> float | None:
    value = row.get("updated_at")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return value.timestamp()  # datetime from psycopg
    except AttributeError:
        return None


class EffectLedger:
    """At-most-once guard for external write tools."""

    def __init__(self, postgres: Any = _UNSET) -> None:
        self._records: dict[str, EffectRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        if postgres is _UNSET:
            postgres = PostgresRepository() if settings.database_url else None
        self._postgres = postgres
        #: Lease takeover counts, exposed for `/ready` so a crashed attempt is visible
        #: rather than only inferable from the metrics endpoint.
        self._takeovers = 0
        self._conflicts = 0

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
            await self._raise_or_log("read effect ledger", effect_key)
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
            updated_at=_updated_at_epoch(row),
        )
        self._records[effect_key] = record
        return record

    async def _raise_or_log(self, operation: str, effect_key: str) -> None:
        if settings.durable_persistence:
            raise RuntimeError(f"Failed to {operation} for {effect_key}")
        logger.warning("Failed to %s for %s; using in-process state", operation, effect_key)

    def _claim(self, existing: EffectRecord | None) -> Claim:
        """Decide whether the effect may run, given the row we read.

        Pure and synchronous on purpose: the decision is the part worth testing, and it must
        not depend on the database being reachable to be correct.
        """
        if existing is None:
            return Claim(run=True, reason="new")
        if existing.status == "succeeded" and existing.response:
            return Claim(run=False, record=existing, reason="deduplicated")
        if existing.status == "failed":
            # The previous attempt is known not to have produced the effect.
            return Claim(run=True, record=existing, reason="retry_after_failure")
        if existing.status != "started":
            return Claim(run=True, record=existing, reason=f"unknown_status:{existing.status}")

        lease = lease_seconds()
        if lease <= 0:
            # Leases disabled: the old behaviour, kept behind an explicit switch so a
            # deployment that depends on it is not changed silently.
            return Claim(run=True, record=existing, reason="lease_disabled")
        updated = existing.updated_at
        if updated is None:
            # No timestamp to reason about: treat it as claimable rather than blocking the
            # write forever. The CAS below still guarantees a single winner.
            return Claim(run=True, record=existing, reason="no_timestamp", takeover=True)
        age = time.time() - updated
        if age < lease:
            return Claim(
                run=False,
                record=existing,
                reason=f"in_progress:{int(lease - age)}s_left",
            )
        return Claim(run=True, record=existing, reason="lease_expired", takeover=True)

    async def run_once(
        self,
        *,
        turn_id: str,
        action: str,
        approval_id: str | None,
        execute: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Execute ``execute(provider_key)`` at most once per turn + action.

        Returns a failure envelope (never raises) when a live lease blocks the effect, so
        the caller can pass ``EFFECT_IN_PROGRESS`` to the model as an explanation.
        """
        effect_key = effect_key_for(turn_id, action)
        provider_key = f"idem_{effect_key[:40]}"

        async with self._lock(effect_key):
            existing = await self._lookup(effect_key)
            claim = self._claim(existing)

            if not claim.run and claim.reason == "deduplicated":
                logger.info("Effect %s already succeeded; skipping write", effect_key)
                return {**existing.response, "deduplicated": True}

            if not claim.run:
                self._conflicts += 1
                metrics.counter(METRIC_LEASE_CONFLICTS, HELP_LEASE_CONFLICTS).inc()
                logger.info("Effect %s is in flight (%s); not replaying", effect_key, claim.reason)
                return {
                    "success": False,
                    "text": "上一次相同操作仍在进行中，本次未重复执行，请稍后再试。",
                    "error": EFFECT_IN_PROGRESS,
                    "lease": claim.reason,
                }

            if claim.takeover:
                # One winner only: the CAS is against the row we read, so a second replay
                # that read the same stale row loses and is told the effect is in progress.
                claimed = await self._take_over(effect_key, claim.record)
                if not claimed:
                    self._conflicts += 1
                    metrics.counter(METRIC_LEASE_CONFLICTS, HELP_LEASE_CONFLICTS).inc()
                    logger.info("Lost the lease race for %s; not replaying", effect_key)
                    return {
                        "success": False,
                        "text": "上一次相同操作仍在进行中，本次未重复执行，请稍后再试。",
                        "error": EFFECT_IN_PROGRESS,
                        "lease": "takeover_lost",
                    }
                self._takeovers += 1
                metrics.counter(METRIC_LEASE_TAKEOVERS, HELP_LEASE_TAKEOVERS).inc()
                logger.warning(
                    "Took over an expired lease for %s (%s); this effect had crashed",
                    effect_key,
                    claim.reason,
                )

            record = EffectRecord(
                effect_key=effect_key,
                turn_id=turn_id,
                approval_id=approval_id,
                action=action,
                status="started",
                provider_key=provider_key,
                attempts=(existing.attempts + 1) if existing else 1,
                response=None,
                updated_at=time.time(),
            )
            self._records[effect_key] = record

            if self._postgres is not None and not claim.takeover:
                # A takeover has already written its row (the CAS); writing again would
                # bump the attempt counter twice for one attempt.
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
            except asyncio.CancelledError:
                # The caller went away. Leave the row `started`: the lease will expire and a
                # replay can take over, which is safer than recording a failure we cannot
                # confirm (the effect may have been applied).
                logger.warning("Effect %s was cancelled mid-flight; lease left to expire", effect_key)
                raise
            except Exception as exc:
                record.status = "failed"
                record.updated_at = time.time()
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
            record.updated_at = time.time()

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

    async def _take_over(self, effect_key: str, record: EffectRecord | None) -> bool:
        """CAS the expired lease. Returns True when this caller owns the effect now.

        The repository decides staleness from its own clock; this method only states how long
        a lease is. Leaving the comparison to the caller's ``updated_at`` reading meant the
        condition was ``updated_at < itself``, which matched nothing and turned every
        legitimate takeover into ``EFFECT_IN_PROGRESS`` -- caught by the lease test rather
        than by a unit test of the decision, because the decision was right.
        """
        if record is None:
            return True
        if self._postgres is None:
            # Single-process deployment: the in-process lock is the whole guarantee, and the
            # decision has already established that the lease expired.
            return True
        try:
            return await self._postgres.claim_stale_effect(
                effect_key,
                lease_seconds=lease_seconds(),
                provider_key=record.provider_key,
            )
        except Exception:
            await self._raise_or_log("claim stale effect", effect_key)
            # Failing closed is the right default for a *write* path: if we cannot prove we
            # own the effect, do not run it.
            return False

    def status(self) -> dict[str, Any]:
        return {
            "leaseSeconds": lease_seconds(),
            "leaseEnabled": bool(settings.effect_lease_enabled),
            "leaseConflicts": self._conflicts,
            "leaseTakeovers": self._takeovers,
            "tracked": len(self._records),
        }

    def reset(self) -> None:
        """Drop the in-process view. For tests, and for an operator's explicit reset.

        The *durable* guarantee lives in the database, so clearing this is safe: the next
        lookup re-reads the row and reaches the same decision. What it changes is whether a
        record written earlier in the same process is still trusted, which is exactly what a
        test needs in order to isolate one scenario from the next.
        """
        self._records.clear()
        self._locks.clear()
        self._takeovers = 0
        self._conflicts = 0


effect_ledger = EffectLedger()
