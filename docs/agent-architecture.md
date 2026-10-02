# Agent Architecture

The repository is split into three runtimes with clear ownership boundaries.

```text
Browser (React/Vite)
  -> Java business service (Spring Boot/WebFlux)
       -> PostgreSQL and Redis
       -> Python agent service (FastAPI/LangGraph/direct model gateway)
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
  - Root graph routes to knowledge, service, or high-risk workflow.
  - Agent and tool permissions are checked at runtime against user roles.
  - High-risk workflows interrupt before any write operation.
  - Enterprise tool execution through replaceable adapters.
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
  - `approval_required`

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

## Approval Checkpoints

High-risk workflows call LangGraph `interrupt()`. The interrupt is only safe to
resume because three things are durable:

1. **The checkpoint.** `AsyncPostgresSaver` stores graph state in its own
   `agent_checkpoint` schema (`CHECKPOINT_BACKEND=postgres`). Production refuses
   to start on the in-memory backend, and there is no silent fallback when
   PostgreSQL is unreachable.
2. **The reference.** On the first interrupt the agent reads
   `checkpoint_id`, `checkpoint_ns` and `interrupt_id` from the graph snapshot and
   writes them into the shared `approval_task` row. Clients never supply these
   fields; they are only echoed back for comparison.
3. **The decision.** Java owns the approval state machine. `pending -> approved`
   / `rejected` is a single compare-and-swap, so only one caller wins, and the
   winner stamps a unique `resume_request_id`.

On Windows the durable backends additionally require a Selector event loop:
psycopg's async driver cannot use `ProactorEventLoop`, which uvicorn selects by
default outside a reload subprocess. Start the agent service with
`python -m app`; see the run section of `agent-service/README.md`.

Resume protocol (`POST /v1/workflows/{turn_id}/resume`, `Accept: application/json`
for service-to-service calls, `text/event-stream` for the frontend):

| Result | Meaning |
| --- | --- |
| `200 resumed` | the decision was applied and the run continued |
| `200 already_resumed` | this `resume_request_id` was already delivered |
| `409 CHECKPOINT_NOT_FOUND` / `STALE_APPROVAL` / `MISSING_CHECKPOINT_REFERENCE` | terminal: no retry, and no new run is started |
| `503 GRAPH_NOT_READY` / `APPROVAL_STORE_UNAVAILABLE` | durability is unavailable; fail closed |

Both outcomes are delivered to the agent. A rejection consumes the interrupt as
well, otherwise the run would stay resumable and could later be replayed as an
approval.

Replay safety rests on three mechanisms:

- `approval_key = sha256(turn_id + action + step)` is unique, so replaying the
  `prepare_approval` node returns the existing task instead of creating a second
  one. The side effect lives in that node, which is *before* the interrupt, so it
  is never re-executed on resume.
- Each resume attempt claims the approval row atomically (`resume_state`
  `queued`/`failed` -> `running`), so the controller and the reconciler — on any
  replica — cannot both deliver it.
- Every write tool runs through an `effect_ledger` row keyed by
  `sha256(turn_id + action)`, and the same key is forwarded to the provider as an
  idempotency key.

`RESUME_RECONCILER_ENABLED` background loops on both sides retry approvals whose
resume never completed, reusing the persisted `resume_request_id`.

## Security Baseline (P0 hardening)

Four controls are load-bearing and must not be relaxed:

1. **Signed caller identity.** The Java service mints a short-lived HS256 JWT per
   agent call (`iss=sky-chat-java-service`, `aud=sky-chat-agent-service`,
   `sub=<user>`, `roles`, 60s TTL). The agent service verifies signature,
   `exp`, `iss` and `aud`, and takes the caller from `sub`. `X-User-ID` is a
   logging hint only — an unsigned request is a 401. `AGENT_INTERNAL_JWT_SECRET`
   is required: with only the legacy static token the agent service would accept
   any claimed identity, so production startup refuses that configuration.
2. **Separation of duties on approvals.** The requester can never decide their
   own approval, and when the rule names approver roles the decider must hold one
   (`admin` always passes). Both checks run *before* the compare-and-swap, so a
   refused attempt leaves the approval claimable. Every decision writes
   `decided_by` / `decided_at` / `decision_comment` and appends an
   `approval_decision` row — an approval can never end up decided without
   attribution.
   Note: the CAS is deliberately **not** scoped to `approval_task.user_id`. That
   column is the requester; scoping the update by it would make separation of
   duties unsatisfiable.
3. **Guardrails by trigger point.** Rails are layered as input / retrieval /
   dialog / execution / output. Retrieved chunks and tool I/O are untrusted and
   are screened for indirect prompt injection and exfiltration markup; flagged
   evidence is quarantined rather than forwarded, and tool arguments are
   allow-listed. Two rules: a rail is a *runtime* layer and never replaces the
   RBAC checks, and every rail can run in `*_log_only` observation mode so a
   rollout does not begin by refusing benign traffic.
4. **Rate limiting and audit.** Redis-backed fixed-window limits protect
   `chat.stream`, `approvals.decide` and `approvals.resume` (per-process fallback
   when Redis is down — a degradation, not a guarantee). `audit_log` records
   administrative and security actions only (never day-to-day reads), with a
   12-month retention policy applied by an operator purge job.

The knowledge index cache is TTL-bounded and explicitly invalidatable
(`invalidate_index()`), and when PostgreSQL is the source of truth the ACL filter
runs in SQL. A permanently cached index was a real defect: revoking a role's
access to a document had no effect until the process restarted.

## Future Work

- Move the in-memory run store to Redis or PostgreSQL.
- Add Temporal for long-running approval workflows.
- Add OpenTelemetry and Langfuse tracing across Java and Python.
