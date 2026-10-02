CREATE EXTENSION IF NOT EXISTS vector;

-- approval_task is the SHARED approval state table. java-service owns its
-- canonical definition (java-service/src/main/resources/schema.sql); this block
-- must stay column-for-column identical so either service can run the DDL first
-- without leaving the other with a mismatched table.
CREATE TABLE IF NOT EXISTS approval_task (
    id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) NULL,
    thread_id VARCHAR(64) NULL,
    checkpoint_id VARCHAR(128) NULL,
    checkpoint_ns VARCHAR(255) NULL,
    interrupt_id VARCHAR(128) NULL,
    user_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(32) NOT NULL,
    intent VARCHAR(128) NULL,
    risk_level VARCHAR(16) NULL,
    rule_id VARCHAR(128) NOT NULL,
    approval_key VARCHAR(128) NULL,
    required_approver_roles VARCHAR(255) NULL,
    resume_token VARCHAR(128) NULL,
    resume_request_id VARCHAR(128) NULL,
    resume_state VARCHAR(16) NOT NULL DEFAULT 'queued',
    resume_attempts INTEGER NOT NULL DEFAULT 0,
    resume_error TEXT NULL,
    consumed_at TIMESTAMP NULL,
    resumed_execution_id VARCHAR(64) NULL,
    status VARCHAR(16) NOT NULL,
    resume_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resumed_at TIMESTAMP NULL
);

-- Upgrades for deployments that created the pre-checkpoint version of the table.
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS turn_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS thread_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS checkpoint_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS checkpoint_ns VARCHAR(255) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS interrupt_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS approval_key VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS required_approver_roles VARCHAR(255) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_token VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_request_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_state VARCHAR(16) NOT NULL DEFAULT 'queued';
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_error TEXT NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMP NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resumed_execution_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMP NULL;
-- resume_payload/result are added by the pre-checkpoint DDL only; keep the guard
-- so a java-service-created table also accepts the agent's in-process fallback.
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS result JSONB NULL;

CREATE INDEX IF NOT EXISTS idx_approval_user ON approval_task(user_id);
-- One approval row per (turn, action, approval step): duplicate high-risk writes
-- must collide here instead of creating a second approval task.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_key
    ON approval_task(approval_key)
    WHERE approval_key IS NOT NULL;
-- One resume request may be delivered at most once, which is what makes the
-- Java CAS claim and the Python replay guard agree.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_resume_request
    ON approval_task(resume_request_id)
    WHERE resume_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approval_resume_queue
    ON approval_task(status, resume_state);

-- Decision attribution. Without these an approved high-risk action cannot be
-- attributed to a human, which is both an audit and a separation-of-duties hole.
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decided_by VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decision_comment TEXT NULL;

-- Append-only decision trail: the approval row holds current state, this holds
-- how it got there (who, when, why), and survives status overwrites.
CREATE TABLE IF NOT EXISTS approval_decision (
    id VARCHAR(64) PRIMARY KEY,
    approval_id VARCHAR(64) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    decided_by VARCHAR(64) NOT NULL,
    decided_by_roles VARCHAR(255) NOT NULL DEFAULT '',
    comment TEXT NULL,
    source_ip VARCHAR(64) NULL,
    resume_request_id VARCHAR(128) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_approval_decision_approval
    ON approval_decision(approval_id, created_at);

-- Administrative/security audit log. Deliberately separate from the business
-- timeline (tickets/approvals): this one records who did what to the platform,
-- carries a retention policy, and must never be mixed with day-to-day updates.
CREATE TABLE IF NOT EXISTS audit_log (
    id VARCHAR(64) PRIMARY KEY,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_id VARCHAR(64) NULL,
    actor_type VARCHAR(16) NOT NULL DEFAULT 'user',
    action VARCHAR(64) NOT NULL,
    object_type VARCHAR(32) NULL,
    object_id VARCHAR(64) NULL,
    outcome VARCHAR(16) NOT NULL DEFAULT 'success',
    source_ip VARCHAR(64) NULL,
    user_agent VARCHAR(255) NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_log_occurred ON audit_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action, occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_object ON audit_log(object_type, object_id);

-- Effect ledger for write tools. A write tool is invoked at most once per
-- idempotency key, so a replayed interrupt node cannot repeat an external write.
CREATE TABLE IF NOT EXISTS effect_ledger (
    effect_key VARCHAR(128) PRIMARY KEY,
    turn_id VARCHAR(64) NOT NULL,
    approval_id VARCHAR(64) NULL,
    action VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    provider_key VARCHAR(128) NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    response JSONB NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_effect_ledger_turn ON effect_ledger(turn_id);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    domain VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    acl JSONB NOT NULL DEFAULT '[]'::jsonb,
    freshness_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_document (
    id VARCHAR(64) PRIMARY KEY,
    kb_id VARCHAR(64) NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'markdown',
    version VARCHAR(64) NOT NULL DEFAULT '1.0',
    checksum VARCHAR(128),
    authority_level INTEGER NOT NULL DEFAULT 0,
    effective_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    allowed_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_chunk (
    id VARCHAR(64) PRIMARY KEY,
    document_id VARCHAR(64) NOT NULL REFERENCES knowledge_document(id) ON DELETE CASCADE,
    parent_chunk_id VARCHAR(64),
    chunk_index INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    section_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    page INTEGER NOT NULL DEFAULT 1,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_case (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    product VARCHAR(64) NOT NULL,
    component VARCHAR(64) NOT NULL,
    environment VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    symptom TEXT NOT NULL,
    error_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_cause TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    closed_at TIMESTAMPTZ,
    allowed_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_event (
    id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL REFERENCES ticket_case(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    actor_hash VARCHAR(128) NOT NULL DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS retrieval_run (
    id VARCHAR(64) PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    plan JSONB NOT NULL DEFAULT '{}'::jsonb,
    subqueries JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    rerank_scores JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_document_kb
    ON knowledge_document(kb_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_document
    ON knowledge_chunk(document_id);
CREATE INDEX IF NOT EXISTS idx_ticket_case_component
    ON ticket_case(component);
CREATE INDEX IF NOT EXISTS idx_ticket_event_case
    ON ticket_event(case_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_run_request
    ON retrieval_run(request_id);
