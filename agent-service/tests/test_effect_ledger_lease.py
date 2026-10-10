"""The effect ledger's lease semantics (T4): a crash must not repeat a write.

The defect this covers was reachable: the ledger only short-circuited on ``succeeded``, so a
process that died between writing ``started`` and writing the terminal status ran the effect
again on replay. ``ticket_create`` and ``ticket_comment`` are writes that no longer require
an approval, so "ran again" meant a second ticket or a second public comment in the user's
name.

Two shapes of test, because two different mechanisms are doing the work:

* **one process, two calls** — the in-process ``asyncio.Lock`` serialises them, and the
  *decision* (``_claim``) is what refuses the replay. The decision is a pure function and is
  tested directly as well as through ``run_once``.
* **two processes** — the in-process lock cannot help, so the guarantee is the repository's
  compare-and-swap. Two ``EffectLedger`` instances standing in for two workers is the only
  way to test that, and it is why the CAS exists.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.effect_ledger import (
    EFFECT_IN_PROGRESS,
    EffectLedger,
    EffectRecord,
    effect_key_for,
    lease_seconds,
)


class FakeRepository:
    """Stand-in that honours the compare-and-swap contract.

    Written by hand rather than mocked so the CAS behaves like the SQL: the update only
    matches a row whose ``updated_at`` is still older than the caller's snapshot, so the
    second of two takeovers matches zero rows and loses.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.finishes: list[tuple[str, str]] = []
        #: Called on every CAS attempt, so a test can hold the row still until both
        #: contenders have read it.
        self.before_claim = None

    async def get_effect(self, effect_key: str):
        row = self.rows.get(effect_key)
        return dict(row) if row else None

    async def begin_effect(self, effect_key, *, turn_id, approval_id, action, provider_key):
        row = self.rows.setdefault(
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
                "updated_at": time.time(),
            },
        )
        row["attempts"] += 1
        row["status"] = "started"
        row["updated_at"] = time.time()
        return dict(row)

    async def finish_effect(self, effect_key, *, status, response=None, error=None):
        row = self.rows.get(effect_key)
        if row is not None:
            row["status"] = status
            row["response"] = response
            row["updated_at"] = time.time()
        self.finishes.append((effect_key, status))

    async def claim_stale_effect(self, effect_key, *, lease_seconds, provider_key=None):
        if self.before_claim is not None:
            await self.before_claim()
        row = self.rows.get(effect_key)
        if row is None or row["status"] != "started":
            return False
        # Same predicate as the SQL: `updated_at < NOW() - lease`, judged on the checker's
        # clock rather than on a timestamp the caller calculated.
        if not (row["updated_at"] < time.time() - lease_seconds):
            return False
        row["attempts"] += 1
        row["updated_at"] = time.time()
        return True


def test_the_fake_repository_agrees_with_the_cas_contract():
    """Guard the guard: the fake must take a stale row and refuse a live one."""
    repo = FakeRepository()
    key = "k"
    repo.rows[key] = _stale_row(key)
    assert asyncio.run(
        repo.claim_stale_effect(key, lease_seconds=lease_seconds(), provider_key="p")
    ) is True, "an expired lease is claimable"

    live = "live"
    repo.rows[live] = {**_stale_row(live), "updated_at": time.time()}
    assert asyncio.run(
        repo.claim_stale_effect(live, lease_seconds=lease_seconds(), provider_key="p")
    ) is False, "a live lease is not claimable"


def _stale_row(key: str) -> dict:
    """A `started` row whose lease is long past: the shape a crash leaves behind."""
    return {
        "effect_key": key,
        "turn_id": "t1",
        "approval_id": None,
        "action": "a",
        "status": "started",
        "provider_key": "idem_old",
        "attempts": 1,
        "response": None,
        "updated_at": time.time() - lease_seconds() - 600,
    }


def _stale_record(key: str) -> EffectRecord:
    """The same row as the ledger caches it, so a test can preload two ledgers."""
    return EffectRecord(
        effect_key=key,
        turn_id="t1",
        approval_id=None,
        action="a",
        status="started",
        provider_key="idem_old",
        attempts=1,
        response=None,
        updated_at=time.time() - lease_seconds() - 600,
    )


def test_a_succeeded_effect_is_not_repeated():
    ledger = EffectLedger(postgres=None)
    calls = {"count": 0}

    async def execute(provider_key):
        calls["count"] += 1
        return {"success": True, "text": "created", "data": {"number": 42}}

    async def scenario():
        first = await ledger.run_once(
            turn_id="t1", action="ticket_create#abc", approval_id=None, execute=execute
        )
        second = await ledger.run_once(
            turn_id="t1", action="ticket_create#abc", approval_id=None, execute=execute
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first["data"]["number"] == 42
    assert calls["count"] == 1
    assert second["deduplicated"] is True
    assert second["data"]["number"] == 42, "a replay returns the original result"


def test_a_failed_effect_may_be_retried():
    ledger = EffectLedger(postgres=None)
    calls = {"count": 0}

    async def execute(provider_key):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"success": False, "text": "ticket service unavailable"}
        return {"success": True, "text": "created"}

    async def scenario():
        await ledger.run_once(turn_id="t1", action="a", approval_id=None, execute=execute)
        return await ledger.run_once(
            turn_id="t1", action="a", approval_id=None, execute=execute
        )

    result = asyncio.run(scenario())
    assert result["success"] is True
    assert calls["count"] == 2, "a known failure must not become permanent"


def test_a_live_lease_refuses_the_replay_and_runs_nothing():
    """The replay is refused by the decision, not by a lock that happened to be held."""
    ledger = EffectLedger(postgres=None)
    key = effect_key_for("t1", "a")
    # The state a crash replay finds: an effect started moments ago by another process.
    ledger._records[key] = EffectRecord(
        effect_key=key,
        turn_id="t1",
        approval_id=None,
        action="a",
        status="started",
        provider_key="idem_live",
        attempts=1,
        response=None,
        updated_at=time.time(),
    )
    calls = {"count": 0}

    async def execute(provider_key):
        calls["count"] += 1
        return {"success": True, "text": "created"}

    async def scenario():
        return await ledger.run_once(
            turn_id="t1", action="a", approval_id=None, execute=execute
        )

    replay = asyncio.run(scenario())
    assert replay["success"] is False
    assert replay["error"] == EFFECT_IN_PROGRESS
    assert replay["lease"].startswith("in_progress")
    assert calls["count"] == 0, "the effect must not run while the original is in flight"
    assert ledger.status()["leaseConflicts"] == 1


def test_an_expired_lease_is_taken_over_once():
    repo = FakeRepository()
    ledger = EffectLedger(postgres=repo)
    key = effect_key_for("t1", "a")
    repo.rows[key] = _stale_row(key)
    calls = {"count": 0}

    async def execute(provider_key):
        calls["count"] += 1
        return {"success": True, "text": "created"}

    async def scenario():
        return await ledger.run_once(
            turn_id="t1", action="a", approval_id=None, execute=execute
        )

    result = asyncio.run(scenario())
    assert result["success"] is True
    assert calls["count"] == 1
    assert ledger.status()["leaseTakeovers"] == 1
    assert repo.rows[key]["attempts"] == 2, "the takeover is recorded as a second attempt"


def test_two_processes_do_not_both_take_over_the_same_lease():
    """The CAS is the guarantee when the in-process lock cannot help.

    Two ``EffectLedger`` instances stand in for two workers so each has its own lock; both
    are held at the CAS barrier until *both* have read the same stale row. Only one update
    can match the row it read, so exactly one effect runs.
    """
    repo = FakeRepository()
    key = effect_key_for("t1", "a")
    repo.rows[key] = _stale_row(key)

    first = EffectLedger(postgres=repo)
    second = EffectLedger(postgres=repo)
    first._records[key] = _stale_record(key)
    second._records[key] = _stale_record(key)

    both_ready = asyncio.Barrier(2)
    gate = {"open": False}

    async def barrier():
        try:
            await asyncio.wait_for(both_ready.wait(), timeout=5)
        except asyncio.TimeoutError:  # pragma: no cover - only on a broken test
            pass
        while not gate["open"]:
            await asyncio.sleep(0.01)

    repo.before_claim = barrier
    executed = {"count": 0}

    async def execute(provider_key):
        executed["count"] += 1
        return {"success": True, "text": "created"}

    async def scenario():
        first_task = asyncio.ensure_future(
            first.run_once(turn_id="t1", action="a", approval_id=None, execute=execute)
        )
        second_task = asyncio.ensure_future(
            second.run_once(turn_id="t1", action="a", approval_id=None, execute=execute)
        )
        await asyncio.sleep(0.1)
        gate["open"] = True
        return await asyncio.gather(first_task, second_task)

    results = asyncio.run(scenario())
    assert executed["count"] == 1, f"both processes ran the effect: {results}"
    outcomes = {result.get("error", "ok") for result in results}
    assert EFFECT_IN_PROGRESS in outcomes, f"the loser was not told: {results}"


def test_cancellation_leaves_the_lease_to_expire():
    """A cancelled call must not record a failure it cannot confirm."""
    repo = FakeRepository()
    ledger = EffectLedger(postgres=repo)
    key = effect_key_for("t1", "a")

    async def execute(provider_key):
        raise asyncio.CancelledError()

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await ledger.run_once(
                turn_id="t1", action="a", approval_id=None, execute=execute
            )

    asyncio.run(scenario())
    # Still `started`: the effect may have been applied, so recording `failed` would invite a
    # retry that duplicates it. The lease expiring is what lets a replay take over safely.
    assert repo.rows[key]["status"] == "started"
    assert (key, "failed") not in repo.finishes


def test_claim_decision_is_pure_and_explicit():
    """The decision table, without a database in the way."""
    ledger = EffectLedger(postgres=None)
    now = time.time()

    assert ledger._claim(None).run is True
    assert ledger._claim(None).reason == "new"

    succeeded = EffectRecord("k", "t", None, "a", "succeeded", "p", 1, {"success": True}, now)
    claim = ledger._claim(succeeded)
    assert claim.run is False and claim.reason == "deduplicated"

    failed = EffectRecord("k", "t", None, "a", "failed", "p", 1, None, now - 3600)
    assert ledger._claim(failed).run is True
    assert ledger._claim(failed).reason == "retry_after_failure"

    live = EffectRecord("k", "t", None, "a", "started", "p", 1, None, now)
    claim = ledger._claim(live)
    assert claim.run is False
    assert claim.reason.startswith("in_progress")

    expired = EffectRecord(
        "k", "t", None, "a", "started", "p", 1, None, now - lease_seconds() - 1
    )
    claim = ledger._claim(expired)
    assert claim.run is True and claim.takeover is True
    assert claim.reason == "lease_expired"


def test_lease_is_derived_from_the_tool_timeout():
    from app.config import settings

    original = settings.tool_timeout_seconds
    try:
        settings.tool_timeout_seconds = 30.0
        assert lease_seconds() >= 60.0, "a 30s tool must not look crashed after 30s"
        assert lease_seconds() >= settings.tool_timeout_seconds * 2
    finally:
        settings.tool_timeout_seconds = original


def test_lease_can_be_disabled_explicitly():
    from app.config import settings

    original = settings.effect_lease_enabled
    try:
        settings.effect_lease_enabled = False
        assert lease_seconds() == 0.0
        ledger = EffectLedger(postgres=None)
        started = EffectRecord("k", "t", None, "a", "started", "p", 1, None, time.time())
        claim = ledger._claim(started)
        assert claim.run is True and claim.reason == "lease_disabled"
    finally:
        settings.effect_lease_enabled = original


def test_takeover_fails_closed_when_the_repository_is_unreachable():
    """If ownership cannot be proven, the write does not run."""

    class BrokenRepository(FakeRepository):
        async def claim_stale_effect(self, *args, **kwargs):
            raise RuntimeError("database is down")

    repo = BrokenRepository()
    # The row must be keyed the way `run_once` looks it up, or the test would silently be
    # exercising the "no existing row" path instead of the takeover path.
    repo.rows[effect_key_for("t1", "a")] = _stale_row(effect_key_for("t1", "a"))
    ledger = EffectLedger(postgres=repo)
    executed = {"count": 0}

    async def execute(provider_key):
        executed["count"] += 1
        return {"success": True, "text": "created"}

    async def scenario():
        return await ledger.run_once(
            turn_id="t1", action="a", approval_id=None, execute=execute
        )

    result = asyncio.run(scenario())
    assert result["error"] == EFFECT_IN_PROGRESS
    assert executed["count"] == 0, "an unprovable claim must not run the write"
