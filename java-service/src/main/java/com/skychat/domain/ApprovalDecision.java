package com.skychat.domain;

import java.time.LocalDateTime;

/**
 * One immutable entry of the approval decision trail.
 *
 * <p>The {@code approval_task} row only carries the latest state; every accepted decision is
 * appended here so a later decision or a manual override never erases who approved what.</p>
 */
public class ApprovalDecision {
    private String id;
    private String approvalId;
    private String decision;
    private String decidedBy;
    private String decidedByRoles;
    private String comment;
    private String sourceIp;
    private String resumeRequestId;
    private LocalDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getApprovalId() {
        return approvalId;
    }

    public void setApprovalId(String approvalId) {
        this.approvalId = approvalId;
    }

    public String getDecision() {
        return decision;
    }

    public void setDecision(String decision) {
        this.decision = decision;
    }

    public String getDecidedBy() {
        return decidedBy;
    }

    public void setDecidedBy(String decidedBy) {
        this.decidedBy = decidedBy;
    }

    public String getDecidedByRoles() {
        return decidedByRoles;
    }

    public void setDecidedByRoles(String decidedByRoles) {
        this.decidedByRoles = decidedByRoles;
    }

    public String getComment() {
        return comment;
    }

    public void setComment(String comment) {
        this.comment = comment;
    }

    public String getSourceIp() {
        return sourceIp;
    }

    public void setSourceIp(String sourceIp) {
        this.sourceIp = sourceIp;
    }

    public String getResumeRequestId() {
        return resumeRequestId;
    }

    public void setResumeRequestId(String resumeRequestId) {
        this.resumeRequestId = resumeRequestId;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
