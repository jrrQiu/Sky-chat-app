CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS user_account (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(320) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    roles VARCHAR(255) NOT NULL DEFAULT 'employee',
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    disabled_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- `CREATE TABLE IF NOT EXISTS` never evolves an existing table, so every column
-- added above must also get a guard here. Without the `roles` one, a database
-- created before that column existed makes every login fail with
-- "column roles does not exist".
ALTER TABLE user_account ADD COLUMN IF NOT EXISTS roles VARCHAR(255) NOT NULL DEFAULT 'employee';
ALTER TABLE user_account ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active';
ALTER TABLE user_account ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS idx_user_account_status ON user_account(status);

-- Invitations replace open self-registration: an administrator names the email and
-- the roles, and the invitee sets their own password through a one-time link.
-- Only the SHA-256 of the token is stored, so a database read cannot be replayed.
CREATE TABLE IF NOT EXISTS user_invitation (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(320) NOT NULL,
    name VARCHAR(120) NULL,
    roles VARCHAR(255) NOT NULL DEFAULT 'employee',
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    invited_by VARCHAR(64) NULL,
    expires_at TIMESTAMP NOT NULL,
    accepted_at TIMESTAMP NULL,
    accepted_by VARCHAR(64) NULL,
    revoked_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE user_invitation ADD COLUMN IF NOT EXISTS name VARCHAR(120) NULL;
ALTER TABLE user_invitation ADD COLUMN IF NOT EXISTS invited_by VARCHAR(64) NULL;
ALTER TABLE user_invitation ADD COLUMN IF NOT EXISTS accepted_by VARCHAR(64) NULL;
ALTER TABLE user_invitation ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS idx_user_invitation_email ON user_invitation(email);
CREATE INDEX IF NOT EXISTS idx_user_invitation_expiry ON user_invitation(expires_at);

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

-- ===========================================================================
-- Service-desk tickets (ITSM layer 1).
--
-- Naming note: `ticket_case` / `ticket_event` already exist and are NOT these.
-- They are the read-only historical corpus that powers similar-ticket search.
-- Live tickets live in `ticket` / `ticket_comment` / `ticket_activity` /
-- `ticket_link`, so the two meanings never share a table.
-- ===========================================================================

-- Human-readable ticket number (#1042). A sequence rather than an identity column so
-- the number is allocated independently of insert order and never reused by a merge.
CREATE SEQUENCE IF NOT EXISTS ticket_number_seq;

CREATE TABLE IF NOT EXISTS ticket (
    id VARCHAR(64) PRIMARY KEY,
    number BIGINT NOT NULL DEFAULT nextval('ticket_number_seq'),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    type VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    -- Priority is derived from impact x urgency at write time (ITIL matrix) but stored,
    -- because it is a decision that was made and must stay reproducible in history.
    priority VARCHAR(16) NOT NULL,
    impact VARCHAR(16) NOT NULL DEFAULT 'medium',
    urgency VARCHAR(16) NOT NULL DEFAULT 'medium',
    title VARCHAR(300) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    requester_id VARCHAR(64) NOT NULL,
    assignee_id VARCHAR(64) NULL,
    parent_id VARCHAR(64) NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'manual',
    -- Trace back to the conversation that raised the ticket, when there was one.
    conversation_id VARCHAR(64) NULL,
    merged_into_id VARCHAR(64) NULL,
    resolution TEXT NULL,
    created_by VARCHAR(64) NOT NULL,
    -- The three SLA timestamps are recorded from day one even though SLA policies are
    -- not built yet: they cannot be reconstructed retroactively, and S2 would otherwise
    -- only be able to measure tickets created after it shipped.
    first_response_at TIMESTAMP NULL,
    resolved_at TIMESTAMP NULL,
    closed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_number ON ticket(number);
CREATE INDEX IF NOT EXISTS idx_ticket_requester ON ticket(requester_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_assignee ON ticket(assignee_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_status ON ticket(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_type ON ticket(type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_parent ON ticket(parent_id);
CREATE INDEX IF NOT EXISTS idx_ticket_merged ON ticket(merged_into_id);

-- Guard columns for a database created before this table existed in its current shape.
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS merged_into_id VARCHAR(64) NULL;
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS first_response_at TIMESTAMP NULL;
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP NULL;
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP NULL;
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(64) NULL;
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS resolution TEXT NULL;

-- The immutable communication log: what the requester and the agent actually said.
-- No update or delete path exists in the service, by design (Znuny keeps communication
-- unchangeable and puts lifecycle changes in a separate history).
CREATE TABLE IF NOT EXISTS ticket_comment (
    id VARCHAR(64) PRIMARY KEY,
    ticket_id VARCHAR(64) NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    author_id VARCHAR(64) NOT NULL,
    author_roles VARCHAR(255) NOT NULL DEFAULT '',
    -- 'internal' is an agent-only note and must never reach a requester.
    visibility VARCHAR(16) NOT NULL DEFAULT 'public',
    body TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticket_comment_ticket ON ticket_comment(ticket_id, created_at);

-- Append-only lifecycle history: who changed what, from what, to what.
CREATE TABLE IF NOT EXISTS ticket_activity (
    id VARCHAR(64) PRIMARY KEY,
    ticket_id VARCHAR(64) NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    actor_id VARCHAR(64) NULL,
    activity_type VARCHAR(32) NOT NULL,
    from_value VARCHAR(120) NULL,
    to_value VARCHAR(120) NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticket_activity_ticket ON ticket_activity(ticket_id, created_at);

-- Relations. Only the declared direction is stored; the inverse ("A blocks B" shows on B
-- as "blocked_by A") is derived when reading, so the two sides cannot drift apart.
CREATE TABLE IF NOT EXISTS ticket_link (
    id VARCHAR(64) PRIMARY KEY,
    ticket_id VARCHAR(64) NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    linked_ticket_id VARCHAR(64) NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
    link_type VARCHAR(24) NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_link
    ON ticket_link(ticket_id, linked_ticket_id, link_type);
CREATE INDEX IF NOT EXISTS idx_ticket_link_source ON ticket_link(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_link_target ON ticket_link(linked_ticket_id);
