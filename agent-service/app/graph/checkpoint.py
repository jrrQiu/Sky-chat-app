from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver

from app.config import settings
from app.runtime import require_async_postgres_supported


@asynccontextmanager
async def open_checkpointer(
    *,
    backend: str | None = None,
    database_url: str | None = None,
    schema_name: str | None = None,
) -> AsyncIterator[Any]:
    """Yield the checkpointer that backs every interrupt.

    Postgres mode fails the caller (and therefore application startup) when the
    driver or the database is unavailable. There is deliberately no fallback to
    `InMemorySaver`: a silent downgrade turns every outstanding approval into an
    unrestorable checkpoint that still reports success.
    """
    backend = backend or settings.checkpoint_backend
    if backend != "postgres":
        if settings.is_production:
            raise RuntimeError(
                "CHECKPOINT_BACKEND must be 'postgres' when ENVIRONMENT=production"
            )
        yield InMemorySaver()
        return

    url = database_url or settings.database_url
    if not url:
        raise RuntimeError(
            "CHECKPOINT_BACKEND=postgres requires DATABASE_URL to be configured"
        )

    # Report an unusable event loop immediately: the driver would otherwise fail
    # every connection attempt and surface as a 30s pool timeout much later.
    require_async_postgres_supported()

    try:
        from psycopg import AsyncConnection, sql
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "CHECKPOINT_BACKEND=postgres requires the 'psycopg[binary]', "
            "'psycopg-pool' and 'langgraph-checkpoint-postgres' packages"
        ) from exc

    schema = schema_name or settings.checkpoint_postgres_schema
    schema_identifier = sql.Identifier(schema)
    options = f"-c search_path={schema_identifier.as_string(None)},public"

    # LangGraph keeps its tables in their own schema so they can never collide
    # with the business tables the Java service owns.
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_identifier)
        )

    async with AsyncConnectionPool(
        conninfo=url,
        min_size=max(1, settings.checkpoint_pool_min_size),
        max_size=settings.checkpoint_pool_max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": options,
        },
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
