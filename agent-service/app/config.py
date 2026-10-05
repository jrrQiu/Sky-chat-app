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
    stream_idle_timeout_seconds: float = 30.0

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
    model_context_window: int = 8192
    output_reserve: int = 1024
    context_safety_margin: int = 256

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
