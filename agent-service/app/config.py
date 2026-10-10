from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    agent_service_token: str = ""
    agent_service_jwt_secret: str = "dev-secret-change-me"

    # --- service-to-service identity -------------------------------------
    # The Java service mints a short-lived HS256 JWT per call; this is the only
    # credential the agent service trusts for a caller identity. A bare
    # `X-User-ID` header is a hint, never an authority.
    agent_internal_jwt_secret: str = ""
    agent_internal_jwt_issuer: str = "sky-chat-java-service"
    agent_internal_jwt_audience: str = "sky-chat-agent-service"
    agent_internal_jwt_leeway_seconds: int = 5
    # Development-only escape hatch for the legacy static shared token.
    allow_static_internal_token: bool = False

    # --- calling back into Java (tool execution) --------------------------
    # The reverse direction uses the SAME shared secret but the opposite
    # issuer/audience pair, so a token minted here is only ever accepted by
    # Java and a token Java minted for us cannot be replayed back at it.
    java_service_base_url: str = "http://localhost:8080"
    agent_caller_issuer: str = "sky-chat-agent-service"
    java_service_audience: str = "sky-chat-java-service"
    agent_caller_ttl_seconds: int = 60
    tool_timeout_seconds: float = 30.0

    # --- rate limiting ----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_prefix: str = "rl"
    rate_limit_default_per_minute: int = 120
    rate_limit_chat_per_minute: int = 60
    rate_limit_approval_per_minute: int = 30
    # When Redis is unreachable the limiter falls back to per-process counters;
    # `fail_open` keeps chat and approvals available instead of locking everyone
    # out. The fallback is per-replica, so it is a degradation, not a guarantee.
    rate_limit_fail_open: bool = True
    # Only honour X-Forwarded-For behind a trusted proxy; otherwise a caller
    # could choose the identity its rate limit and audit log are keyed on.
    trust_forwarded_for: bool = False

    # --- audit ------------------------------------------------------------
    audit_enabled: bool = True
    audit_retention_months: int = 12

    # --- guardrails -------------------------------------------------------
    # Retrieved chunks and tool I/O are untrusted input. The retrieval rail
    # quarantines flagged evidence (drops it) instead of forwarding it to the
    # model; `guardrails_log_only` degrades that to observation for rollouts.
    retrieval_rail_enabled: bool = True
    retrieval_rail_log_only: bool = False
    tool_rail_enabled: bool = True
    tool_rail_log_only: bool = False
    # The knowledge index is cached in-process; a short TTL plus explicit
    # invalidation is what makes a revoked permission take effect.
    index_cache_ttl_seconds: float = 30.0

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    # The stream's idle window is the *outermost* of the three time budgets and must be
    # strictly larger than the node idle window, which is in turn larger than a single
    # tool's timeout. When they were all 30s, a legitimate 25-30s tool looked like a hung
    # stream and the turn was killed mid-execution.
    stream_idle_timeout_seconds: float = 120.0

    # Which adapter serves tools the ticket adapter does not implement. `openapi` POSTs to
    # `tool_base_url`; anything else refuses, because the mock provider that used to answer here
    # was deleted -- it returned hardcoded strings that read like successful integrations.
    tool_provider: str = "internal"
    tool_base_url: str = ""
    tool_api_token: str = ""
    # Whether a problem described in chat automatically raises a real ticket. On by
    # default because that is the point of a service desk, but an operator can turn
    # it off without losing the ticket tools themselves.
    ticket_auto_create_from_chat: bool = True
    # Whether the model chooses tools (function calling) instead of the rule-based
    # fallback. Requires a model API key; `/ready` reports which mode is actually in
    # effect under `tools.routing`, so the two are not confused.
    function_calling_enabled: bool = True
    #: Model used for the tool-selection turns. Empty means "the same model as the answer",
    #: which is the default because a deployment that reasons with one model and decides
    #: with another is a configuration nobody expects.
    function_calling_model: str = ""
    # The agent loop is bounded. An agent that can keep calling tools needs a ceiling on
    # rounds and on total calls, or a confused model turns into an unbounded bill --
    # and, with real writes, into repeated effects.
    agent_max_rounds: int = 3
    agent_max_tool_calls: int = 6
    # Total tokens the loop may spend before it stops choosing tools and answers with what it
    # has. 0 disables the check (the call caps still apply).
    agent_token_budget: int = 20000
    # Stop after a successful state-changing call: the write *is* the outcome, and another round
    # could only add duplicate writes or reads the answer can already be written from.
    agent_stop_after_write: bool = True
    # Stream the tool *decision* so the user sees the reasoning as it happens instead of a
    # generic "thinking" line. Costs one streaming request per round.
    agent_stream_decisions: bool = True

    # --- tool resilience (retry / circuit breaker / time budget) -----------
    # T1/T2: retries are bounded per call *and* in total, so "each call retries twice"
    # cannot add up to an unbounded round. `is_safe_to_retry` decides whether a given
    # exception may be retried at all, and is kept semantically identical to the Java
    # `AgentCircuitBreaker.isSafeToRetry`.
    tool_retry_read_attempts: int = 3  # total attempts (1 initial + 2 retries)
    tool_retry_write_attempts: int = 2  # writes retry only when the request never landed
    tool_retry_initial_backoff_seconds: float = 0.2
    tool_retry_backoff_factor: float = 3.0
    tool_retry_max_backoff_seconds: float = 2.0
    tool_retry_jitter: float = 0.25
    #: Ceiling on the whole retry budget for one call, including the waits.
    tool_retry_total_budget_seconds: float = 8.0
    #: Hard wall-clock cap for ONE tool call, retries included, enforced with `asyncio.wait_for`
    #: around the invocation. httpx's own timeout is per operation and its read timeout measures
    #: the gap *between* reads, so a dependency that drips a byte every few seconds would never
    #: time out and could hold the node for its entire run budget.
    #:
    #: It must stay **below `effect_lease_seconds`**: a write that is still running when its
    #: lease expires can be taken over by a replay and executed twice, which is exactly what the
    #: lease exists to prevent. `EffectLedger.lease_seconds` derives its floor from this value.
    tool_call_deadline_seconds: float = 45.0
    #: Per-tool circuit breaker: consecutive transport failures before the breaker opens.
    tool_circuit_failure_threshold: int = 3
    tool_circuit_cooldown_seconds: float = 30.0
    #: Heartbeat cadence while a tool runs. Keeps the node/stream idle windows alive and
    #: gives the client something to show instead of a frozen "thinking" line.
    tool_heartbeat_seconds: float = 10.0
    #: Node-level idle window. Must sit between one tool's timeout and the stream's.
    node_idle_timeout_seconds: float = 60.0

    # --- effect ledger lease (T4) ------------------------------------------
    #: How long a `started` row is trusted to mean "someone is working on it". Derived from
    #: the tool timeout so a legitimately long call is not mistaken for a crash, and a
    #: replayed turn after a real crash can eventually take over.
    effect_lease_seconds: float = 90.0
    #: When true, a live lease makes a replay return EFFECT_IN_PROGRESS instead of running.
    effect_lease_enabled: bool = True

    # --- write authorisation tiers (T7) ------------------------------------
    #: Tools whose `write_policy` is `confirm` pause the graph for one user confirmation
    #: before executing. Off means the confirm tier degrades to `direct`, which `/ready`
    #: reports, so "the agent can create tickets silently" is never an accident.
    write_confirmation_enabled: bool = True
    #: Tokens allowed for one tool result before it is trimmed for the model. The full text
    #: stays in the emitted event, so "what the tool said" is still auditable.
    tool_result_max_tokens: int = 1500
    # When an approved high-risk action has no integrated target system, register the
    # approved work as a change ticket in our own system of record. The external action
    # still reports as not executed; this only ensures the approval leaves a trace.
    high_risk_records_change_ticket: bool = True
    # How long an approval stays usable for a system-executed action. A system-executed
    # action is an elevation path, so an old approval must not act as a standing warrant.
    # 0 disables the check (not recommended).
    system_action_approval_ttl_seconds: int = 86400

    retrieval_use_llm: bool = False
    retrieval_use_postgres: bool = False
    intent_use_llm: bool = False
    intent_use_semantic: bool = True
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    # `HashEmbeddingProvider` is a deterministic bag-of-tokens stand-in: its
    # vectors carry no semantic signal, so fusing them makes "hybrid" retrieval
    # worse than lexical-only. Opt-in for offline development and tests only; the
    # service otherwise runs (and reports) lexical-only retrieval.
    allow_hash_embeddings: bool = False
    # Texts per `/embeddings` request. One request per chunk was the old shape: an
    # N+1 round-trip pattern on every query. Providers accept far more than this;
    # 64 keeps a single request small enough to retry cheaply.
    embedding_batch_size: int = 64
    # Embeddings are content-addressed and cached, so a repeated query or an
    # unchanged document costs nothing. The key includes the model name and
    # dimension, because reusing vectors across models would silently corrupt
    # ranking rather than error.
    embedding_cache_enabled: bool = True
    embedding_cache_size: int = 4096

    # --- dense retrieval storage -----------------------------------------
    # Chunk vectors are written to `knowledge_chunk.embedding` (pgvector) at
    # ingest and searched with an HNSW index. With this off, or with no
    # database, the dense channel falls back to scanning the in-process index —
    # correct but O(n) per query, and the reason `/ready` reports the backend.
    vector_store_enabled: bool = True
    # HNSW build/query parameters. `ef_search` is set per query; a higher value
    # trades latency for recall.
    vector_hnsw_m: int = 16
    vector_hnsw_ef_construction: int = 64
    vector_hnsw_ef_search: int = 40
    # Prompt budget for one policy evidence block. A matched chunk is expanded with
    # its siblings so a qualifying clause in the next sentence is not lost, and cut
    # here so a hit inside a long policy cannot push out every other source.
    retrieval_evidence_max_tokens: int = 320
    # Per-document cap on evidence blocks. Without it one verbose policy can occupy
    # the whole context window, which is the "no dedup" anti-pattern.
    retrieval_evidence_per_document: int = 3

    # --- context engine ---------------------------------------------------
    # The window and the reserved output are **not** configured here any more:
    # they come from `app/context/model_specs.py`, so the budget and the
    # `max_tokens` actually sent to the provider cannot drift apart. These two
    # remain as explicit overrides for an operator who must pin a value without a
    # code change (0 = use the model spec).
    model_context_window: int = 0
    output_reserve: int = 0
    # Head-room for the tokenizer's approximation of the provider's tokenizer and
    # for protocol overhead. Applied to every request.
    context_safety_margin: int = 256
    # When false the engine evicts without summarising: report still records every
    # drop, so the loss is measurable rather than invisible.
    context_summary_enabled: bool = True
    # Shadow mode plans the prompt with the new engine and reports the decisions
    # but keeps serving the recorded prompt. For rollout comparison.
    context_shadow_mode: bool = False
    # A model the vendor no longer publishes a spec for is either refused (true)
    # or served with the conservative default spec and reported as `known: false`.
    strict_model_allowlist: bool = False

    redis_url: str = "redis://localhost:6379/0"
    # A down Redis must not add seconds of latency to every streamed event; the
    # publisher degrades to its in-process buffer instead.
    redis_connect_timeout_seconds: float = 0.5
    redis_socket_timeout_seconds: float = 1.0
    redis_degrade_after_failures: int = 3
    redis_degrade_seconds: float = 30.0
    database_url: str = ""
    environment: str = "development"
    checkpoint_backend: str = "memory"
    checkpoint_postgres_schema: str = "agent_checkpoint"
    checkpoint_pool_min_size: int = 1
    checkpoint_pool_max_size: int = 10

    # A resumed graph re-enters the interrupted node, so the durability contract
    # is "checkpoint refs are always persisted before a resume is accepted".
    resume_max_attempts: int = 5
    resume_claim_timeout_seconds: int = 120
    resume_reconciler_interval_seconds: float = 30.0
    resume_reconciler_enabled: bool = True
    strict_persistence: bool = False

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    otel_exporter_otlp_endpoint: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def durable_persistence(self) -> bool:
        """True when PostgreSQL is the configured system of record.

        In this mode a persistence error must surface instead of silently
        degrading to the in-process fallback, because a lost approval row or a
        lost checkpoint reference is exactly what makes a resume unsafe.
        """
        return bool(self.database_url) and (
            self.strict_persistence or self.checkpoint_backend == "postgres"
        )

    def effective_context_limits(self, model: str | None) -> tuple[int, int]:
        """``(context_window, max_output)`` for one model.

        Single source for both numbers: the budget subtracts the second from the
        first, and the same second value is sent as ``max_tokens``. `.env` values
        act as overrides; ``0`` means "use the model spec".
        """
        from app.context.model_specs import model_spec

        spec = model_spec(model)
        window = self.model_context_window or spec.context_window
        max_output = self.output_reserve or spec.default_max_output
        # An inverted pair would make the usable budget negative and every
        # request infeasible, so it is clamped here rather than left to fail deep
        # inside the planner.
        if max_output >= window:
            max_output = max(1, window // 2)
        return int(window), int(max_output)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
