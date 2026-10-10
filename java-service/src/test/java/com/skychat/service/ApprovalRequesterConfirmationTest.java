package com.skychat.service;

import com.skychat.domain.ApprovalTask;
import com.skychat.mapper.ApprovalDecisionMapper;
import com.skychat.mapper.ApprovalMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The one hole in separation of duties: a write *confirmation*.
 *
 * <p>An agent that wants to create a ticket in a user's name asks that user. That is not the
 * same thing as approving somebody else's high-risk request, so the requester has to be able
 * to answer -- and only when the record says so, via the literal `requester` marker. Blank
 * must stay "no self-decision", or the exception would silently become the rule.</p>
 */
class ApprovalRequesterConfirmationTest {

    private static final String REQUESTER = "user-requester";

    private ApprovalService serviceWith(ApprovalTask task) {
        return new ApprovalService(new SingleRowMapper(task), new NoDecisions());
    }

    private ApprovalTask pending(String requiredRoles) {
        ApprovalTask task = new ApprovalTask();
        task.setId("approval_1");
        task.setUserId(REQUESTER);
        task.setStatus("pending");
        task.setRequiredApproverRoles(requiredRoles);
        task.setResumeState("queued");
        return task;
    }

    @Test
    void the_marker_is_recognised_and_nothing_else_is() {
        assertThat(ApprovalService.isRequesterAnswered("requester")).isTrue();
        assertThat(ApprovalService.isRequesterAnswered("requester,approver")).isTrue();
        assertThat(ApprovalService.isRequesterAnswered("approver")).isFalse();
        assertThat(ApprovalService.isRequesterAnswered("")).isFalse();
        assertThat(ApprovalService.isRequesterAnswered(null)).isFalse();
    }

    @Test
    void the_requester_can_answer_a_write_confirmation() {
        ApprovalService service = serviceWith(pending("requester"));

        ApprovalService.Decision decision = service.decide(
                REQUESTER, "approval_1", "approve", "确认建单", List.of("employee"), "127.0.0.1"
        );

        assertThat(decision.failure()).isNull();
        assertThat(decision.newlyDecided()).isTrue();
        assertThat(decision.task().getStatus()).isEqualTo("approved");
        assertThat(decision.task().getDecidedBy()).isEqualTo(REQUESTER);
    }

    @Test
    void the_requester_still_cannot_approve_their_own_high_risk_request() {
        ApprovalService service = serviceWith(pending("network_admin"));

        ApprovalService.Decision decision = service.decide(
                REQUESTER, "approval_1", "approve", null, List.of("network_admin"), "127.0.0.1"
        );

        assertThat(decision.failure()).isEqualTo(ApprovalService.Failure.SELF_APPROVAL);
        assertThat(decision.newlyDecided()).isFalse();
    }

    @Test
    void a_blank_requirement_still_forbids_self_decision() {
        // Blank means "anybody may decide", and that must not include the requester: reading it
        // that way would turn the exception into the rule.
        ApprovalService service = serviceWith(pending(null));

        ApprovalService.Decision decision = service.decide(
                REQUESTER, "approval_1", "approve", null, List.of("employee"), "127.0.0.1"
        );

        assertThat(decision.failure()).isEqualTo(ApprovalService.Failure.SELF_APPROVAL);
    }

    @Test
    void somebody_else_can_answer_a_write_confirmation_for_the_requester() {
        ApprovalService service = serviceWith(pending("requester"));

        ApprovalService.Decision decision = service.decide(
                "another-user", "approval_1", "reject", "不是本人操作", List.of("employee"), "127.0.0.1"
        );

        assertThat(decision.failure()).isNull();
        assertThat(decision.newlyDecided()).isTrue();
        assertThat(decision.task().getStatus()).isEqualTo("rejected");
    }

    /** One stored row, so the CAS path is exercised without a database. */
    private static final class SingleRowMapper implements ApprovalMapper {
        private ApprovalTask stored;

        SingleRowMapper(ApprovalTask stored) {
            this.stored = stored;
        }

        private ApprovalTask find(String id) {
            return stored != null && stored.getId().equals(id) ? stored : null;
        }

        @Override
        public ApprovalTask findByIdInternal(String id) {
            return find(id);
        }

        @Override
        public ApprovalTask findById(String id, String userId) {
            return find(id);
        }

        @Override
        public ApprovalTask findLatestPending(String userId) {
            return null;
        }

        @Override
        public List<ApprovalTask> findByUserId(String userId) {
            return List.of();
        }

        @Override
        public List<ApprovalTask> findResumeCandidates(int limit) {
            return List.of();
        }

        @Override
        public List<ApprovalTask> findPendingForApprover(
                String userId, String roles, boolean isAdmin, int limit) {
            return List.of();
        }

        @Override
        public int insert(ApprovalTask task) {
            return 0;
        }

        @Override
        public int upsertFromEvent(ApprovalTask task) {
            return 0;
        }

        @Override
        public int updateStatus(
                String id,
                String status,
                String resumeRequestId,
                String resumedExecutionId,
                java.time.LocalDateTime resumedAt,
                String decidedBy,
                java.time.LocalDateTime decidedAt,
                String decisionComment,
                java.time.LocalDateTime updatedAt
        ) {
            if (stored == null || !stored.getId().equals(id) || !"pending".equals(stored.getStatus())) {
                return 0;
            }
            stored.setStatus(status);
            stored.setDecidedBy(decidedBy);
            stored.setDecisionComment(decisionComment);
            return 1;
        }

        @Override
        public int markResumeResult(
                String id,
                String resumeState,
                String resumeError,
                java.time.LocalDateTime consumedAt,
                java.time.LocalDateTime updatedAt
        ) {
            return 0;
        }
    }

    private static final class NoDecisions implements ApprovalDecisionMapper {
        @Override
        public int insert(com.skychat.domain.ApprovalDecision decision) {
            return 0;
        }

        @Override
        public List<com.skychat.domain.ApprovalDecision> findByApprovalId(String approvalId) {
            return List.of();
        }
    }
}
