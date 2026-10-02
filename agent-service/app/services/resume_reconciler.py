import asyncio
import logging
from typing import Any, Callable

from app.config import settings
from app.persistence.approval_store import ApprovalStoreUnavailable, approval_store
from app.services.approval_resume import resolve_approval

logger = logging.getLogger(__name__)


async def reconcile_once(graph) -> int:
    """Retry approvals whose resume never completed.

    Covers the crash windows the synchronous path cannot: the process died
    between committing the decision and delivering the resume, or the resume
    itself failed transiently. Claims are atomic and the resume request id is
    stable, so running this on every replica is safe.
    """
    if graph is None:
        return 0
    try:
        candidates = await approval_store.list_resume_candidates()
    except ApprovalStoreUnavailable:
        logger.exception("Resume reconciler could not read candidates")
        return 0

    delivered = 0
    for task in candidates:
        try:
            resolution = await resolve_approval(task, graph=graph)
        except Exception:
            logger.exception("Resume reconciler failed for approval %s", task.id)
            continue
        if resolution.status in {"resumed", "already_resumed", "interrupted"}:
            delivered += 1
        elif resolution.error not in {None, "RESUME_IN_PROGRESS"}:
            logger.warning(
                "Resume reconciler left approval %s in state %s (%s)",
                task.id,
                resolution.status,
                resolution.error,
            )
    return delivered


class ResumeReconciler:
    """Background loop that periodically calls `reconcile_once`."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self,
        graph_provider: Callable[[], Any],
        interval_seconds: float | None = None,
    ) -> None:
        if not settings.resume_reconciler_enabled or self.running:
            return
        self._stopped = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(graph_provider, interval_seconds or settings.resume_reconciler_interval_seconds)
        )

    async def stop(self) -> None:
        self._stopped.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _loop(
        self,
        graph_provider: Callable[[], Any],
        interval_seconds: float,
    ) -> None:
        interval = max(5.0, float(interval_seconds))
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await reconcile_once(graph_provider())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Resume reconciler tick failed")


resume_reconciler = ResumeReconciler()
