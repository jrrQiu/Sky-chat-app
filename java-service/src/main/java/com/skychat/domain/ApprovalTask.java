package com.skychat.domain;

import java.time.LocalDateTime;

public class ApprovalTask {
    private String id;
    private String runId;
    private String turnId;
    private String threadId;
    private String checkpointId;
    private String checkpointNs;
    private String interruptId;
    private String approvalKey;
    private String resumeRequestId;
    private String resumeState;
    private Integer resumeAttempts;
    private String resumeError;
    private LocalDateTime consumedAt;
    private String userId;
    private String agentId;
    private String intent;
    private String riskLevel;
    private String ruleId;
    private String requiredApproverRoles;
    private String resumeToken;
    private String resumedExecutionId;
    private String status;
    private String decidedBy;
    private LocalDateTime decidedAt;
    private String decisionComment;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime resumedAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getRunId() {
        return runId;
    }

    public void setRunId(String runId) {
        this.runId = runId;
    }

    public String getTurnId() {
        return turnId;
    }

    public void setTurnId(String turnId) {
        this.turnId = turnId;
    }

    public String getThreadId() {
        return threadId;
    }

    public void setThreadId(String threadId) {
        this.threadId = threadId;
    }

    public String getCheckpointId() {
        return checkpointId;
    }

    public void setCheckpointId(String checkpointId) {
        this.checkpointId = checkpointId;
    }

    public String getCheckpointNs() {
        return checkpointNs;
    }

    public void setCheckpointNs(String checkpointNs) {
        this.checkpointNs = checkpointNs;
    }

    public String getInterruptId() {
        return interruptId;
    }

    public void setInterruptId(String interruptId) {
        this.interruptId = interruptId;
    }

    public String getApprovalKey() {
        return approvalKey;
    }

    public void setApprovalKey(String approvalKey) {
        this.approvalKey = approvalKey;
    }

    public String getResumeRequestId() {
        return resumeRequestId;
    }

    public void setResumeRequestId(String resumeRequestId) {
        this.resumeRequestId = resumeRequestId;
    }

    public String getResumeState() {
        return resumeState;
    }

    public void setResumeState(String resumeState) {
        this.resumeState = resumeState;
    }

    public int getResumeAttempts() {
        return resumeAttempts == null ? 0 : resumeAttempts;
    }

    public void setResumeAttempts(Integer resumeAttempts) {
        this.resumeAttempts = resumeAttempts;
    }

    public String getResumeError() {
        return resumeError;
    }

    public void setResumeError(String resumeError) {
        this.resumeError = resumeError;
    }

    public LocalDateTime getConsumedAt() {
        return consumedAt;
    }

    public void setConsumedAt(LocalDateTime consumedAt) {
        this.consumedAt = consumedAt;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getAgentId() {
        return agentId;
    }

    public void setAgentId(String agentId) {
        this.agentId = agentId;
    }

    public String getIntent() {
        return intent;
    }

    public void setIntent(String intent) {
        this.intent = intent;
    }

    public String getRiskLevel() {
        return riskLevel;
    }

    public void setRiskLevel(String riskLevel) {
        this.riskLevel = riskLevel;
    }

    public String getRuleId() {
        return ruleId;
    }

    public void setRuleId(String ruleId) {
        this.ruleId = ruleId;
    }

    public String getRequiredApproverRoles() {
        return requiredApproverRoles;
    }

    public void setRequiredApproverRoles(String requiredApproverRoles) {
        this.requiredApproverRoles = requiredApproverRoles;
    }

    public String getResumeToken() {
        return resumeToken;
    }

    public void setResumeToken(String resumeToken) {
        this.resumeToken = resumeToken;
    }

    public String getResumedExecutionId() {
        return resumedExecutionId;
    }

    public void setResumedExecutionId(String resumedExecutionId) {
        this.resumedExecutionId = resumedExecutionId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getDecidedBy() {
        return decidedBy;
    }

    public void setDecidedBy(String decidedBy) {
        this.decidedBy = decidedBy;
    }

    public LocalDateTime getDecidedAt() {
        return decidedAt;
    }

    public void setDecidedAt(LocalDateTime decidedAt) {
        this.decidedAt = decidedAt;
    }

    public String getDecisionComment() {
        return decisionComment;
    }

    public void setDecisionComment(String decisionComment) {
        this.decisionComment = decisionComment;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }

    public LocalDateTime getResumedAt() {
        return resumedAt;
    }

    public void setResumedAt(LocalDateTime resumedAt) {
        this.resumedAt = resumedAt;
    }
}
