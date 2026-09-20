# Agent Architecture

The repository is split into three runtimes with clear ownership boundaries.

```text
Browser (React/Vite)
  -> Java business service (Spring Boot/WebFlux)
       -> PostgreSQL and Redis
       -> Python agent service (FastAPI/LangGraph/LiteLLM)
```

## Responsibilities

### React frontend

- Sends login, conversation, message, and SSE requests to the Java service.
- Keeps message state, streaming state, tool invocation state, and UI state in
  local Zustand stores.
- Never talks to the Python service directly.

### Java business service

- Issues and verifies local JWT tokens.
- Persists users, conversations, messages, and approval tasks.
- Converts frontend chat requests into the Python agent request contract.
- Proxies agent SSE events back to the browser.
- Keeps an in-memory run replay buffer so clients can continue after a dropped
  SSE connection.

### Python agent service

- Implements the LangGraph pipeline:
  - Guardian input/output checks.
  - Deterministic request orchestration.
  - Knowledge retrieval.
  - Enterprise tool execution through replaceable adapters.
  - High-risk approval creation.
  - Final model generation.
- Returns normalized SSE events:
  - `thinking`
  - `answer`
  - `tool_call`
  - `tool_progress`
  - `tool_result`
  - `conversation_title`
  - `error`
  - `complete`

## Chat Request Flow

1. The React client POSTs a chat request to `/v1/chat/stream`.
2. The Java service authenticates with the local JWT filter and injects
   `X-User-ID`.
3. The Java service persists the user message and forwards a normalized
   snake_case payload to `/v1/chat/stream` on the Python service.
4. The Python service streams events back to Java.
5. Java appends sequence IDs, publishes events to the run store, and forwards
   the SSE response to the browser.
6. On completion, Java persists the assistant answer and optional generated
   conversation title.

## Run Recovery

`GET /v1/chat/runs/{runId}` accepts `Last-Event-ID` or `?lastSeq=` and replays
events after that sequence. `DELETE /v1/chat/runs/{runId}` cancels an active
run.

## Future Work

- Move the in-memory run store to Redis or PostgreSQL.
- Add Temporal for long-running approval workflows.
- Add OpenTelemetry and Langfuse tracing across Java and Python.
