CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS user_account (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(320) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    roles VARCHAR(255) NOT NULL DEFAULT 'employee',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- `CREATE TABLE IF NOT EXISTS` never evolves an existing table, so every column
-- added to a CREATE statement above must also get a guard here. Without this one,
-- a database created before `roles` existed makes every login fail with
-- "column roles does not exist".
ALTER TABLE user_account ADD COLUMN IF NOT EXISTS roles VARCHAR(255) NOT NULL DEFAULT 'employee';

CREATE TABLE IF NOT EXISTS conversation (
    id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
    pinned_at TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS message (
    id VARCHAR(64) PRIMARY KEY,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_message_conversation ON message(conversation_id);

CREATE TABLE IF NOT EXISTS approval_task (
    id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    checkpoint_id VARCHAR(128) NULL,
    checkpoint_ns VARCHAR(255) NULL,
    interrupt_id VARCHAR(128) NULL,
    approval_key VARCHAR(128) NULL,
    resume_request_id VARCHAR(128) NULL,
    resume_state VARCHAR(16) NOT NULL DEFAULT 'queued',
    resume_attempts INTEGER NOT NULL DEFAULT 0,
    resume_error TEXT NULL,
    consumed_at TIMESTAMP NULL,
    user_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(32) NOT NULL,
    intent VARCHAR(128) NULL,
    risk_level VARCHAR(16) NULL,
    rule_id VARCHAR(128) NOT NULL,
    required_approver_roles VARCHAR(255) NULL,
    resume_token VARCHAR(128) NULL,
    resumed_execution_id VARCHAR(64) NULL,
    status VARCHAR(16) NOT NULL,
    decided_by VARCHAR(64) NULL,
    decided_at TIMESTAMP NULL,
    decision_comment TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resumed_at TIMESTAMP NULL
);

ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS turn_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS thread_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS checkpoint_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS required_approver_roles VARCHAR(255) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_token VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resumed_execution_id VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMP NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS checkpoint_ns VARCHAR(255) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS interrupt_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS approval_key VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_request_id VARCHAR(128) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_state VARCHAR(16) NOT NULL DEFAULT 'queued';
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS resume_error TEXT NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMP NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decided_by VARCHAR(64) NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP NULL;
ALTER TABLE approval_task ADD COLUMN IF NOT EXISTS decision_comment TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_approval_user ON approval_task(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_key ON approval_task(approval_key) WHERE approval_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_resume_request ON approval_task(resume_request_id) WHERE resume_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approval_resume_queue ON approval_task(status, resume_state);

CREATE TABLE IF NOT EXISTS user_profile (
    user_id VARCHAR(64) PRIMARY KEY,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    facts JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_summary (
    conversation_id VARCHAR(64) NOT NULL,
    version INT NOT NULL,
    summary TEXT NOT NULL,
    start_message_id VARCHAR(64) NULL,
    end_message_id VARCHAR(64) NULL,
    token_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, version)
);

CREATE TABLE IF NOT EXISTS memory (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    expires_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_summary_conversation
    ON conversation_summary(conversation_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_memory_user
    ON memory(user_id, kind);

-- Approval decision trail: one immutable row per accepted decision, so the
-- approval_task row keeps only the latest state.
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
CREATE INDEX IF NOT EXISTS idx_approval_decision_approval ON approval_decision(approval_id, created_at);

-- Administrative/security audit trail. Read-only endpoints are deliberately
-- not recorded here (Zammad rule: audit administrative actions, not day-to-day reads).
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
