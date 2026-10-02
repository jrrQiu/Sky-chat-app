"""Runtime concerns that must be settled before the event loop exists.

psycopg's async driver uses `add_reader`/`add_writer`, which Windows'
`ProactorEventLoop` does not implement:

    Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

uvicorn hardcodes `ProactorEventLoop` on win32 unless it is running a reload
subprocess, and it injects that loop through `asyncio.Runner(loop_factory=...)`
rather than through the event loop policy. Setting a policy is therefore not
enough: when a durable backend is configured, the server must be started through
`app.runtime.run_server()` (i.e. `python -m app`).
"""

import asyncio
import sys
from typing import Any, Callable

LoopFactory = Callable[[], asyncio.AbstractEventLoop]


def selector_loop_factory() -> LoopFactory | None:
    """The loop factory a durable backend needs, or None to keep the default."""
    if sys.platform != "win32":
        return None
    return asyncio.SelectorEventLoop


def configure_event_loop_policy() -> str:
    """Select a psycopg-compatible policy for callers that use `asyncio.run`.

    Tests and scripts create their loop through `asyncio.run()`, which honours
    the policy, so this is enough for them. The server needs
    `run_server()` instead, because uvicorn bypasses the policy.
    """
    if sys.platform != "win32":
        return type(asyncio.get_event_loop_policy()).__name__
    policy_class = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_class is not None:
        asyncio.set_event_loop_policy(policy_class())
    return type(asyncio.get_event_loop_policy()).__name__


def async_postgres_loop_problem() -> str | None:
    """Describe why the running loop cannot host the async driver, if so."""
    if sys.platform != "win32":
        return None
    try:
        loop: Any = asyncio.get_running_loop()
    except RuntimeError:
        return None
    name = type(loop).__name__
    if "Proactor" not in name:
        return None
    return (
        f"psycopg's async driver cannot run on {name}. Start the service with "
        "`python -m app` (which selects a Selector loop) or run uvicorn with "
        "`--reload`; see agent-service/README.md."
    )


def require_async_postgres_supported() -> None:
    """Fail fast instead of waiting for a 30s pool timeout on every request."""
    problem = async_postgres_loop_problem()
    if problem:
        raise RuntimeError(problem)


def run_server(host: str | None = None, port: int | None = None) -> None:
    """Run uvicorn on a loop the async PostgreSQL driver accepts."""
    import uvicorn

    from app.config import settings

    config = uvicorn.Config(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    factory = selector_loop_factory()
    if factory is None:
        server.run()
        return

    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(server.serve())
