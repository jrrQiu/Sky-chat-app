"""The durable backends must refuse an event loop psycopg cannot drive.

Windows' `ProactorEventLoop` does not implement `add_reader`/`add_writer`, so
psycopg's async driver raises

    Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

uvicorn selects that loop on win32 unless it runs a reload subprocess, so the
service detects it and fails with an actionable message instead of a 30 second
pool timeout on the first request.
"""

import asyncio
import sys

import pytest

from app.runtime import (
    async_postgres_loop_problem,
    configure_event_loop_policy,
    require_async_postgres_supported,
    selector_loop_factory,
)

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="ProactorEventLoop exists only on Windows",
)


def test_the_test_environment_itself_is_supported():
    """The guard must not fire under the loop the suite runs on."""

    async def scenario():
        return async_postgres_loop_problem()

    assert asyncio.run(scenario()) is None
    require_async_postgres_supported()


def test_configure_event_loop_policy_selects_a_supported_policy():
    name = configure_event_loop_policy()
    if sys.platform == "win32":
        assert "Selector" in name, name
    else:
        assert name  # any policy is fine off Windows


@windows_only
def test_proactor_loop_is_detected_and_refused():
    async def scenario():
        problem = async_postgres_loop_problem()
        assert problem is not None
        assert "Proactor" in problem
        assert "python -m app" in problem
        with pytest.raises(RuntimeError, match="Proactor"):
            require_async_postgres_supported()
        return problem

    loop = asyncio.ProactorEventLoop()
    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()


@windows_only
def test_selector_factory_is_offered_on_windows():
    factory = selector_loop_factory()
    assert factory is asyncio.SelectorEventLoop
    loop = factory()
    try:
        assert "Selector" in type(loop).__name__
    finally:
        loop.close()
