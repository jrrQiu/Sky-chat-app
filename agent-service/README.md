# Sky Chat Agent Service

FastAPI + LangGraph agent service for `sky-chat-app`. Model calls use a
direct HTTP gateway, so startup does not depend on LiteLLM initialization.

## Install

```powershell
cd D:\VueProject\sky-chat-app\agent-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional integrations:

```powershell
python -m pip install -r requirements-optional.txt
```

## Run

```powershell
python -m app
```

`python -m app` is the supported entrypoint once a durable backend is in use.
uvicorn chooses `ProactorEventLoop` on Windows unless it is running a reload
subprocess, and psycopg's async driver cannot run on it:

```text
Psycopg cannot use the 'ProactorEventLoop' to run in async mode.
```

uvicorn injects that loop through `asyncio.Runner(loop_factory=...)`, so setting
an event loop policy does not help; `app/runtime.py` starts uvicorn on a Selector
loop itself. The checkpointer and the approval store also detect a Proactor loop
and fail fast with an actionable message instead of a 30 second pool timeout.

`uvicorn app.main:app --reload` works too (uvicorn then uses a Selector loop),
and on Linux and macOS plain `uvicorn app.main:app` is fine.

Then configure the Java business service:

```env
AGENT_SERVICE_URL=http://localhost:8000
AGENT_SERVICE_TOKEN=replace-with-internal-jwt
```

## Main Endpoints

```text
GET   /health
GET   /ready
POST  /v1/chat/stream
GET   /v1/approvals
PATCH /v1/approvals/{id}/decision
POST  /v1/workflows/{turn_id}/resume
```

The chat stream endpoint returns normalized SSE events compatible with the
React frontend through the Java service proxy. `/v1/workflows/{turn_id}/resume`
serves both callers: `Accept: text/event-stream` streams the resumed run for the
frontend, and `Accept: application/json` returns a single JSON result for the
Java service.

## Durable Approvals

High-risk runs interrupt with LangGraph `interrupt()`. Resuming one requires a
durable checkpoint, the checkpoint reference, and a single-winner decision:

```env
ENVIRONMENT=development
CHECKPOINT_BACKEND=postgres
CHECKPOINT_POSTGRES_SCHEMA=agent_checkpoint
RESUME_RECONCILER_ENABLED=true
RESUME_RECONCILER_INTERVAL_SECONDS=30
RESUME_MAX_ATTEMPTS=5
RESUME_CLAIM_TIMEOUT_SECONDS=120
```

`CHECKPOINT_BACKEND=memory` keeps local development working and is what the test
suite uses. With `ENVIRONMENT=production` the service refuses to start unless
the backend is `postgres`: a checkpoint lost on restart is an approval that can
never be resumed. The same rule applies to the approval store — when
`DATABASE_URL` is configured, a persistence failure surfaces as
`503 APPROVAL_STORE_UNAVAILABLE` instead of silently falling back to memory.

See `docs/agent-architecture.md` for the full resume protocol and its
idempotency guarantees.

## Tool Adapters

Set `TOOL_PROVIDER=mock` for the current deterministic adapters. When real
enterprise APIs are available, set:

```env
TOOL_PROVIDER=openapi
TOOL_BASE_URL=https://internal-tools.example.com
TOOL_API_TOKEN=replace-with-tool-token
```

The service will call `POST {TOOL_BASE_URL}/{tool_name}` for each tool. Every
write carries an `Idempotency-Key` header derived from the effect ledger, so a
replayed tool call can be deduplicated by the provider.

## Event Stream Degradation

Redis Streams backs run replay, with an in-process buffer as fallback. A failed
Redis is detected with short connect/socket timeouts and, after
`REDIS_DEGRADE_AFTER_FAILURES` consecutive failures, skipped for
`REDIS_DEGRADE_SECONDS` so a Redis outage does not add latency to every streamed
event. Each transition is logged once.

## Retrieval

The knowledge path now routes policy queries, cross-knowledge-base queries, and
historical ticket investigations through `app/retrieval/pipeline.py`.

Optional runtime switches:

```env
RETRIEVAL_USE_LLM=false
RETRIEVAL_USE_POSTGRES=false
```

With `RETRIEVAL_USE_POSTGRES=true`, the service bootstraps the schema in
`schema.sql`, seeds the bundled corpus into PostgreSQL, and loads the runtime
index from PostgreSQL on startup. Set `DATABASE_URL` to the target database.

Run the local evaluation suite:

```powershell
python -m eval.run_eval
python -m pytest tests/test_retrieval_pipeline.py tests/test_evaluation.py
```

## Server Context

The browser no longer sends the full message list. Java rebuilds authoritative
recent messages, user profile, rolling summary, approval state, and eligible
memories into `context_envelope`. Python assembles the model prompt with
`app/context/builder.py`, applies token budgets, filters expired tool results,
and records `context_hash` plus per-block token usage.

```env
MODEL_CONTEXT_WINDOW=8192
OUTPUT_RESERVE=1024
CONTEXT_SAFETY_MARGIN=256
```

## Intent Routing

Orchestrator now resolves `domain`, `operation`, `intent`, `risk`, and
`confidence` separately. Rules live in `app/intent/rules.yaml`; a hash/semantic
classifier runs below the rule fast path, and `INTENT_USE_LLM=true` enables the
structured LLM fallback.

```env
INTENT_USE_LLM=false
INTENT_USE_SEMANTIC=true
```
