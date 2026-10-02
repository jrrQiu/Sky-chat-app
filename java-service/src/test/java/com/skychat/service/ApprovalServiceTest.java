package com.skychat.service;

import com.skychat.domain.ApprovalDecision;
import com.skychat.domain.ApprovalTask;
import com.skychat.mapper.ApprovalDecisionMapper;
import com.skychat.mapper.ApprovalMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Separation of duties, the required-approver-role gate and the decision trail.
 *
 * <p>Fakes implement the two mappers: Mockito's inline mock maker cannot attach on this JDK, and
 * a hand-written fake lets the CAS behave exactly like the SQL
 * ({@code WHERE id = ? AND status = 'pending'}) so the "status unchanged" assertions are
 * meaningful.</p>
 */
class ApprovalServiceTest {

    private static final String APPROVAL_ID = "approval_1";
    private static final String REQUESTER = "user-requester";
    private static final String DECIDER = "user-approver";

    private final FakeApprovalMapper approvalMapper = new FakeApprovalMapper();
    private final FakeDecisionMapper decisionMapper = new FakeDecisionMapper();
    private final ApprovalService service = new ApprovalService(approvalMapper, decisionMapper);

    // ------------------------------------------------------------------ fakes

    private static final class FakeApprovalMapper implements ApprovalMapper {
        private ApprovalTask stored;

        @Override
        public ApprovalTask findByIdInternal(String id) {
            return stored != null && stored.getId().equals(id) ? stored : null;
        }

        @Override
        public ApprovalTask findById(String id, String userId) {
            ApprovalTask task = findByIdInternal(id);
            return task != null && userId.equals(task.getUserId()) ? task : null;
        }

        @Override
        public List<ApprovalTask> findByUserId(String userId) {
            return stored != null && userId.equals(stored.getUserId()) ? List.of(stored) : List.of();
        }

        @Override
        public int updateStatus(
                String id,
                String status,
                String resumeRequestId,
                String resumedExecutionId,
                LocalDateTime resumedAt,
                String decidedBy,
                LocalDateTime decidedAt,
                String decisionComment,
                LocalDateTime updatedAt
        ) {
            // Mirrors the SQL: the single-winner claim is id + status='pending' only. Note that
            // user_id (the requester) is deliberately absent from the predicate.
            if (stored == null || !stored.getId().equals(id) || !"pending".equals(stored.getStatus())) {
                return 0;
            }
            stored.setStatus(status);
            stored.setResumeRequestId(resumeRequestId);
            stored.setResumedExecutionId(resumedExecutionId);
            stored.setResumedAt(resumedAt);
            stored.setDecidedBy(decidedBy);
            stored.setDecidedAt(decidedAt);
            stored.setDecisionComment(decisionComment);
            stored.setUpdatedAt(updatedAt);
            return 1;
        }

        @Override
        public ApprovalTask findLatestPending(String userId) {
            return null;
        }

        @Override
        public List<ApprovalTask> findResumeCandidates(int limit) {
            return List.of();
        }

        /**
         * Mirrors the SQL approver queue: pending rows only, never the requester's own, and the
         * role test is skipped for an admin or when no roles are required.
         */
        @Override
        public List<ApprovalTask> findPendingForApprover(
                String userId,
                String roles,
                boolean isAdmin,
                int limit
        ) {
            if (stored == null
                    || !"pending".equals(stored.getStatus())
                    || userId.equals(stored.getUserId())) {
                return List.of();
            }
            String required = stored.getRequiredApproverRoles();
            if (isAdmin || required == null || required.isBlank()) {
                return List.of(stored);
            }
            Set<String> held = roles == null || roles.isBlank()
                    ? Set.of()
                    : Arrays.stream(roles.split(","))
                            .map(String::trim)
                            .filter(value -> !value.isEmpty())
                            .collect(Collectors.toSet());
            boolean matches = Arrays.stream(required.split(","))
                    .map(String::trim)
                    .anyMatch(held::contains);
            return matches ? List.of(stored) : List.of();
        }

        @Override
        public int insert(ApprovalTask task) {
            stored = task;
            return 1;
        }

        @Override
        public int upsertFromEvent(ApprovalTask task) {
            stored = task;
            return 1;
        }

        @Override
        public int markResumeResult(
                String id,
                String resumeState,
                String resumeError,
                LocalDateTime consumedAt,
                LocalDateTime updatedAt
        ) {
            return stored == null ? 0 : 1;
        }
    }

    private static final class FakeDecisionMapper implements ApprovalDecisionMapper {
        private final List<ApprovalDecision> rows = new ArrayList<>();

        @Override
        public int insert(ApprovalDecision decision) {
            rows.add(decision);
            return 1;
        }

        @Override
        public List<ApprovalDecision> findByApprovalId(String approvalId) {
            return rows.stream()
                    .filter(row -> approvalId.equals(row.getApprovalId()))
                    .toList();
        }
    }

    // ------------------------------------------------------------------ helpers

    private ApprovalTask pendingTask(String requiredRoles) {
        ApprovalTask task = new ApprovalTask();
        task.setId(APPROVAL_ID);
        task.setRunId("run_1");
        task.setTurnId("turn_1");
        task.setThreadId("turn_1");
        task.setUserId(REQUESTER);
        task.setAgentId("approval");
        task.setRuleId("HIGH-RISK-APPROVAL-REQUIRED");
        task.setRequiredApproverRoles(requiredRoles);
        task.setStatus("pending");
        task.setResumeState("queued");
        task.setCreatedAt(LocalDateTime.now());
        task.setUpdatedAt(LocalDateTime.now());
        approvalMapper.stored = task;
        return task;
    }

    // ------------------------------------------------------------------ separation of duties

    @Test
    @DisplayName("the requester cannot approve their own request: SELF_APPROVAL, status unchanged")
    void requesterCannotSelfApprove() {
        pendingTask("admin");

        ApprovalService.Decision decision = service.decide(
                REQUESTER, APPROVAL_ID, "approve", "looks fine", List.of("admin"), "10.0.0.1");

        assertThat(decision.failure()).isEqualTo(ApprovalService.Failure.SELF_APPROVAL);
        assertThat(decision.newlyDecided()).isFalse();
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("pending");
        assertThat(approvalMapper.stored.getDecidedBy()).isNull();
        assertThat(decisionMapper.rows).isEmpty();
    }

    @Test
    @DisplayName("a different user holding the required role can decide")
    void differentApproverWithRoleCanDecide() {
        pendingTask("approver");

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", "approved by the approver", List.of("approver"), "10.0.0.2");

        assertThat(decision.failure()).isNull();
        assertThat(decision.newlyDecided()).isTrue();
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("approved");
        assertThat(approvalMapper.stored.getDecidedBy()).isEqualTo(DECIDER);
    }

    // ------------------------------------------------------------------ required roles

    @Test
    @DisplayName("a decider without the required role is refused: ROLE_REQUIRED, status unchanged")
    void deciderWithoutTheRequiredRoleIsRefused() {
        pendingTask("finance-approver");

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", null, List.of("employee"), "10.0.0.3");

        assertThat(decision.failure()).isEqualTo(ApprovalService.Failure.ROLE_REQUIRED);
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("pending");
        assertThat(decisionMapper.rows).isEmpty();
    }

    @Test
    @DisplayName("holding one of several required roles is enough")
    void anyOfTheRequiredRolesIsEnough() {
        pendingTask("finance-approver, security-approver");

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", null, List.of("employee", "security-approver"), "10.0.0.4");

        assertThat(decision.failure()).isNull();
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("approved");
    }

    @Test
    @DisplayName("the literal admin role always passes the gate")
    void adminAlwaysPasses() {
        pendingTask("finance-approver");

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", null, List.of("admin"), "10.0.0.5");

        assertThat(decision.failure()).isNull();
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("approved");
    }

    @Test
    @DisplayName("admin passes even when the requirement uses different casing")
    void adminCheckIsCaseInsensitive() {
        pendingTask("finance-approver");

        assertThat(ApprovalService.hasRequiredRole("finance-approver", List.of("ADMIN"))).isTrue();
        assertThat(ApprovalService.hasRequiredRole("Finance-Approver", List.of("finance-approver")))
                .isTrue();
        assertThat(ApprovalService.hasRequiredRole("finance-approver", List.of("employee")))
                .isFalse();
    }

    @Test
    @DisplayName("an empty requirement leaves the decision open to any non-requester")
    void emptyRequirementIsOpen() {
        pendingTask(null);

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", null, List.of("employee"), "10.0.0.6");

        assertThat(decision.failure()).isNull();
        assertThat(approvalMapper.stored.getStatus()).isEqualTo("approved");
    }

    // ------------------------------------------------------------------ trail and audit fields

    @Test
    @DisplayName("a decision writes decided_by/decided_at/decision_comment and appends a trail row")
    void decisionPersistsWhoAndWhy() {
        pendingTask("approver");
        LocalDateTime before = LocalDateTime.now();

        ApprovalService.Decision decision = service.decide(
                DECIDER,
                APPROVAL_ID,
                "reject",
                "  missing the vendor contract  ",
                List.of("approver", "employee"),
                "203.0.113.7"
        );

        assertThat(decision.newlyDecided()).isTrue();

        ApprovalTask persisted = approvalMapper.stored;
        assertThat(persisted.getStatus()).isEqualTo("rejected");
        assertThat(persisted.getDecidedBy()).isEqualTo(DECIDER);
        assertThat(persisted.getDecidedAt()).isNotNull();
        assertThat(persisted.getDecidedAt()).isAfterOrEqualTo(before.minusSeconds(1));
        assertThat(persisted.getDecisionComment()).isEqualTo("  missing the vendor contract  ");

        assertThat(decisionMapper.rows).hasSize(1);
        ApprovalDecision trail = decisionMapper.rows.get(0);
        assertThat(trail.getApprovalId()).isEqualTo(APPROVAL_ID);
        assertThat(trail.getDecision()).isEqualTo("rejected");
        assertThat(trail.getDecidedBy()).isEqualTo(DECIDER);
        assertThat(trail.getDecidedByRoles()).isEqualTo("approver,employee");
        assertThat(trail.getComment()).isEqualTo("  missing the vendor contract  ");
        assertThat(trail.getSourceIp()).isEqualTo("203.0.113.7");
        assertThat(trail.getResumeRequestId()).isEqualTo(persisted.getResumeRequestId());
        assertThat(trail.getCreatedAt()).isNotNull();
        assertThat(trail.getId()).isNotBlank();
    }

    @Test
    @DisplayName("exactly one trail row is appended per successful decision")
    void oneTrailRowPerDecision() {
        pendingTask("approver");

        service.decide(DECIDER, APPROVAL_ID, "approve", null, List.of("approver"), "10.0.0.8");
        // A second call loses the CAS (the row is no longer pending) and must not add a row.
        service.decide(DECIDER, APPROVAL_ID, "approve", null, List.of("approver"), "10.0.0.8");

        assertThat(decisionMapper.rows).hasSize(1);
        assertThat(service.decisions(APPROVAL_ID)).hasSize(1);
    }

    @Test
    @DisplayName("an unknown approval is NOT_FOUND and nothing is written")
    void unknownApproval() {
        ApprovalService.Decision decision = service.decide(
                DECIDER, "approval_missing", "approve", null, List.of("admin"), null);

        assertThat(decision.failure()).isEqualTo(ApprovalService.Failure.NOT_FOUND);
        assertThat(decision.task()).isNull();
        assertThat(decisionMapper.rows).isEmpty();
    }

    @Test
    @DisplayName("a decision that loses the CAS reports alreadyDecided with the persisted state")
    void losingTheCasReportsAlreadyDecided() {
        ApprovalTask task = pendingTask("approver");
        task.setStatus("approved");
        task.setDecidedBy("someone-else");
        task.setResumeRequestId("rsq_existing");

        ApprovalService.Decision decision = service.decide(
                DECIDER, APPROVAL_ID, "approve", null, List.of("approver"), "10.0.0.9");

        assertThat(decision.failure()).isNull();
        assertThat(decision.newlyDecided()).isFalse();
        assertThat(decision.alreadyDecided()).isTrue();
        assertThat(decision.task().getResumeRequestId()).isEqualTo("rsq_existing");
        assertThat(decisionMapper.rows).isEmpty();
    }

    @Test
    @DisplayName("a rejection is recorded as 'rejected' in the trail, not as the raw action")
    void rejectionTrailUsesThePersistedStatus() {
        pendingTask(null);

        service.decide(DECIDER, APPROVAL_ID, "reject", null, List.of(), null);

        assertThat(decisionMapper.rows.get(0).getDecision()).isEqualTo("rejected");
        assertThat(decisionMapper.rows.get(0).getDecidedByRoles()).isEmpty();
    }
}
