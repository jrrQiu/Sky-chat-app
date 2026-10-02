package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.ApprovalTask;
import com.skychat.domain.AuditLog;
import com.skychat.mapper.AuditLogMapper;
import com.skychat.service.ApprovalResumeGuard;
import com.skychat.service.ApprovalService;
import com.skychat.service.AuditService;
import com.skychat.service.WorkflowResumeClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import reactor.core.publisher.Mono;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Decision matrix for {@code PATCH /v1/approvals/{id}/decision}.
 *
 * <p>No database and no mocking framework: the collaborators are hand-written fakes so the
 * test needs no bytecode agent (Mockito's inline mock maker cannot attach on every JDK).
 * The point is the routing logic around the compare-and-set, the single-delivery
 * guarantee, the separation-of-duties gate, and how each agent outcome maps onto a status
 * code.</p>
 */
class ApprovalControllerTest {

    private static final String USER = "u1";
    private static final String APPROVAL = "approval_abc";
    private static final String RESUME_REQUEST_ID = "rsq_1";

    private FakeApprovalService approvalService;
    private FakeResumeClient resumeClient;
    private FakeAuditService auditService;
    private ApprovalController controller;

    @BeforeEach
    void setUp() {
        approvalService = new FakeApprovalService();
        resumeClient = new FakeResumeClient();
        auditService = new FakeAuditService();
        controller = new ApprovalController(
                approvalService,
                resumeClient,
                new ApprovalResumeGuard(),
                auditService,
                new ClientAddressResolver(null)
        );
    }

    // ------------------------------------------------------------------ fakes

    /** Records every call so the tests can assert on the exact resume payload. */
    private static final class FakeApprovalService extends ApprovalService {
        private ApprovalTask decisionTask;
        private boolean newlyDecided;
        private boolean alreadyDecided;
        private boolean notFound;
        private boolean applyPolicy;
        private Failure failure;
        private String lastComment;

        private final List<String> resumeSucceeded = new ArrayList<>();
        private final List<String> resumeFailed = new ArrayList<>();
        private final List<String> resumeStale = new ArrayList<>();

        /** The approver queue the fake service reports. */
        private List<ApprovalTask> queue = new ArrayList<>();

        FakeApprovalService() {
            super(null, null);
        }

        @Override
        public List<ApprovalTask> listToApprove(String userId, List<String> roles, int limit) {
            return queue;
        }

        @Override
        public List<ApprovalTask> listAll(String userId, List<String> roles, int limit) {
            // Mirrors the real merge: newest-first keyed by id, so an approval that is
            // both mine and decidable by me appears once.
            java.util.LinkedHashMap<String, ApprovalTask> merged = new java.util.LinkedHashMap<>();
            for (ApprovalTask task : listToApprove(userId, roles, limit)) {
                merged.put(task.getId(), task);
            }
            if (decisionTask != null) {
                merged.putIfAbsent(decisionTask.getId(), decisionTask);
            }
            return List.copyOf(merged.values());
        }

        @Override
        public Decision decide(
                String userId,
                String id,
                String action,
                String comment,
                List<String> deciderRoles,
                String sourceIp
        ) {
            if (notFound) {
                return Decision.refused(null, Failure.NOT_FOUND);
            }
            if (failure != null) {
                return Decision.refused(decisionTask, failure);
            }
            if (applyPolicy && decisionTask != null) {
                // Mirrors ApprovalService: authorise, then claim. A self-approval is refused
                // before anything is written, and the required-role gate allows admin through.
                if (userId != null && userId.equals(decisionTask.getUserId())) {
                    return Decision.refused(decisionTask, Failure.SELF_APPROVAL);
                }
                if (!hasRequiredRole(decisionTask.getRequiredApproverRoles(), deciderRoles)) {
                    return Decision.refused(decisionTask, Failure.ROLE_REQUIRED);
                }
            }
            lastComment = comment;
            if (decisionTask != null && newlyDecided) {
                // The real service persists the outcome in the same call, so a freshly claimed
                // decision has to be reflected here: the controller branches on the status it
                // reads back. A lost compare-and-set leaves the stored status untouched, which
                // is exactly what the conflicting-repeat case relies on.
                decisionTask.setStatus("approve".equals(action) ? "approved" : "rejected");
            }
            return Decision.allowed(decisionTask, newlyDecided, alreadyDecided);
        }

        @Override
        public ApprovalTask findByIdInternal(String id) {
            return decisionTask;
        }

        @Override
        public List<ApprovalTask> list(String userId) {
            return List.of(decisionTask);
        }

        @Override
        public void markResumeSucceeded(String id) {
            resumeSucceeded.add(id);
        }

        @Override
        public void markResumeFailed(String id, String error) {
            resumeFailed.add(id + ":" + error);
        }

        @Override
        public void markResumeStale(String id, String error) {
            resumeStale.add(id + ":" + error);
        }
    }

    private static final class FakeResumeClient extends WorkflowResumeClient {
        private final List<String> calls = new ArrayList<>();
        private WorkflowResumeClient.ResumeResult result =
                new WorkflowResumeClient.ResumeResult(
                        WorkflowResumeClient.Outcome.SUCCEEDED, "resumed", null);

        FakeResumeClient() {
            super(null, null, null, null);
        }

        @Override
        public Mono<ResumeResult> resume(
                ApprovalTask task,
                String decision,
                String resumeRequestId
        ) {
            calls.add(task.getId() + ":" + decision + ":" + resumeRequestId);
            return Mono.just(result);
        }
    }

    /**
     * Captures the audit rows instead of writing them, so the decision path stays testable
     * without a database.
     */
    private static final class FakeAuditService extends AuditService {
        private record Row(String actorId, String action, String objectId, String outcome, Map<String, Object> detail) {
        }

        private final List<Row> rows = new ArrayList<>();

        FakeAuditService() {
            super(new NoOpAuditLogMapper(), new com.fasterxml.jackson.databind.ObjectMapper());
        }

        @Override
        public void record(
                Context context,
                String action,
                String objectType,
                String objectId,
                String outcome,
                Map<String, Object> detail
        ) {
            rows.add(new Row(
                    context == null ? null : context.actorId(),
                    action,
                    objectId,
                    outcome,
                    detail == null ? Map.of() : Map.copyOf(detail)
            ));
        }
    }

    private static final class NoOpAuditLogMapper implements AuditLogMapper {
        @Override
        public int insert(
                String id,
                LocalDateTime occurredAt,
                String actorId,
                String actorType,
                String action,
                String objectType,
                String objectId,
                String outcome,
                String sourceIp,
                String userAgent,
                String detailJson
        ) {
            return 1;
        }
    }

    // ---------------------------------------------------------------- helpers

    private ApprovalTask task(String status, String resumeState) {
        ApprovalTask task = new ApprovalTask();
        task.setId(APPROVAL);
        task.setRunId("turn_1");
        task.setTurnId("turn_1");
        task.setThreadId("turn_1");
        task.setUserId(USER);
        task.setStatus(status);
        task.setResumeState(resumeState);
        task.setResumeRequestId(RESUME_REQUEST_ID);
        task.setCheckpointId("cp_1");
        task.setCheckpointNs("");
        task.setInterruptId("int_1");
        return task;
    }

    private void decideReturns(ApprovalTask task, boolean newlyDecided, boolean alreadyDecided) {
        approvalService.decisionTask = task;
        approvalService.newlyDecided = newlyDecided;
        approvalService.alreadyDecided = alreadyDecided;
        approvalService.failure = null;
    }

    private void decideRefused(ApprovalTask task, ApprovalService.Failure failure) {
        approvalService.decisionTask = task;
        approvalService.failure = failure;
    }

    private void resumeReturns(
            WorkflowResumeClient.Outcome outcome,
            String status,
            String error
    ) {
        resumeClient.result = new WorkflowResumeClient.ResumeResult(outcome, status, error);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> body(ResponseEntity<Object> response) {
        assertThat(response.getBody()).isNotNull();
        return (Map<String, Object>) response.getBody();
    }

    private ResponseEntity<Object> decide(Map<String, String> request) {
        return controller.decide(USER, "admin,employee", APPROVAL, request, null).block();
    }

    // ------------------------------------------------------------------ tests

    @Test
    @DisplayName("an invalid action is rejected before anything is claimed")
    void invalidAction() {
        ResponseEntity<Object> response = decide(Map.of("action", "maybe"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(400);
        assertThat(resumeClient.calls).isEmpty();
    }

    @Test
    @DisplayName("an unknown or unowned approval is a 404 and never reaches the agent")
    void unknownApproval() {
        approvalService.notFound = true;

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(404);
        assertThat(body(response).get("error")).isEqualTo("APPROVAL_NOT_FOUND");
        assertThat(resumeClient.calls).isEmpty();
    }

    @Test
    @DisplayName("a rejection is delivered to the agent so the interrupt is consumed")
    void rejectionIsDelivered() {
        ApprovalTask rejected = task("rejected", "queued");
        decideReturns(rejected, true, false);

        ResponseEntity<Object> response = decide(Map.of("action", "reject"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        // Without this call the run would stay interrupted and the approval could
        // still be replayed as "approved" through the agent's own resume endpoint.
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":rejected:" + RESUME_REQUEST_ID);
        assertThat(approvalService.resumeSucceeded).containsExactly(APPROVAL);
    }

    @Test
    @DisplayName("an approval is delivered to the agent exactly once")
    void approvalIsDeliveredOnce() {
        decideReturns(task("approved", "queued"), true, false);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":approved:" + RESUME_REQUEST_ID);
    }

    @Test
    @DisplayName("a repeated click after a successful resume never re-runs the agent")
    void repeatedClickIsIdempotent() {
        decideReturns(task("approved", "succeeded"), false, true);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(resumeClient.calls).isEmpty();
        // A crash-recovery retry re-delivers the SAME persisted decision; it is not a new
        // decision, so no second success row is written.
        assertThat(auditService.rows).isEmpty();
    }

    @Test
    @DisplayName("a decision that was persisted but never delivered is retried with the same id")
    void crashRecoveryReusesThePersistedRequestId() {
        decideReturns(task("approved", "failed"), false, true);
        resumeReturns(WorkflowResumeClient.Outcome.SUCCEEDED, "already_resumed", null);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":approved:" + RESUME_REQUEST_ID);
    }

    @Test
    @DisplayName("a repeated rejection that never reached the agent is retried as a rejection")
    void repeatedRejectionIsRetried() {
        decideReturns(task("rejected", "failed"), false, true);

        ResponseEntity<Object> response = decide(Map.of("action", "reject"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":rejected:" + RESUME_REQUEST_ID);
    }

    @Test
    @DisplayName("a duplicate click while the first attempt is in flight is not duplicated")
    void inFlightAttemptIsNotDuplicated() {
        decideReturns(task("approved", "queued"), true, false);
        // Pre-acquire the guard to simulate a concurrent request holding it.
        ApprovalResumeGuard guard = new ApprovalResumeGuard();
        assertThat(guard.tryAcquire(APPROVAL)).isTrue();
        ApprovalController blocked = new ApprovalController(
                approvalService, resumeClient, guard, auditService, new ClientAddressResolver(null));

        ResponseEntity<Object> response = blocked
                .decide(USER, "", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(202);
        // The claim already happened, so the retry must reuse the same request id.
        assertThat(resumeClient.calls).isEmpty();
        guard.release(APPROVAL);
    }

    @Test
    @DisplayName("an already-decided approval with a different outcome is a 409")
    void conflictingRepeatIs409() {
        decideReturns(task("rejected", "succeeded"), false, true);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(409);
        assertThat(resumeClient.calls).isEmpty();
        // A conflicting repeat is security relevant: it is audited as a denied attempt.
        assertThat(auditService.rows).hasSize(1);
        assertThat(auditService.rows.get(0).outcome()).isEqualTo(AuditService.OUTCOME_DENIED);
        assertThat(auditService.rows.get(0).detail().get("reason"))
                .isEqualTo("APPROVAL_ALREADY_DECIDED");
    }

    @Test
    @DisplayName("a stale checkpoint is terminal: 409 and never retried")
    void staleCheckpointIs409() {
        decideReturns(task("approved", "queued"), true, false);
        resumeReturns(WorkflowResumeClient.Outcome.STALE, null, "STALE_APPROVAL");

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(409);
        assertThat(body(response).get("error")).isEqualTo("STALE_APPROVAL");
        assertThat(approvalService.resumeStale).containsExactly(APPROVAL + ":STALE_APPROVAL");
        assertThat(approvalService.resumeSucceeded).isEmpty();
    }

    @Test
    @DisplayName("a missing checkpoint is reported as CHECKPOINT_NOT_FOUND")
    void missingCheckpointIsTerminal() {
        decideReturns(task("approved", "queued"), true, false);
        resumeReturns(WorkflowResumeClient.Outcome.STALE, null, "CHECKPOINT_NOT_FOUND");

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(409);
        assertThat(approvalService.resumeStale)
                .containsExactly(APPROVAL + ":CHECKPOINT_NOT_FOUND");
    }

    @Test
    @DisplayName("a transient failure is retryable and returns 202")
    void transientFailureIsRetryable() {
        decideReturns(task("approved", "queued"), true, false);
        resumeReturns(
                WorkflowResumeClient.Outcome.TRANSIENT_FAILURE,
                null,
                "connection refused"
        );

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(202);
        @SuppressWarnings("unchecked")
        Map<String, Object> resume = (Map<String, Object>) body(response).get("resume");
        assertThat(resume.get("retryable")).isEqualTo(true);
        assertThat(approvalService.resumeFailed)
                .containsExactly(APPROVAL + ":connection refused");
    }

    @Test
    @DisplayName("an approval unknown to the agent is parked as stale")
    void agentNotFoundIsParked() {
        decideReturns(task("approved", "queued"), true, false);
        resumeReturns(WorkflowResumeClient.Outcome.NOT_FOUND, null, null);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(409);
        assertThat(approvalService.resumeStale)
                .containsExactly(APPROVAL + ":APPROVAL_NOT_FOUND");
    }

    @Test
    @DisplayName("an unexpected 200 payload is retryable, not a success")
    void unexpectedSuccessPayloadIsRetried() {
        decideReturns(task("approved", "queued"), true, false);
        resumeReturns(
                WorkflowResumeClient.Outcome.TRANSIENT_FAILURE,
                "weird",
                "UNEXPECTED_RESUME_STATUS:weird"
        );

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(202);
    }

    @Test
    @DisplayName("the list endpoint defaults to the caller's own requests")
    void listIsUserScoped() {
        approvalService.decisionTask = task("pending", "queued");

        ResponseEntity<List<ApprovalTask>> response =
                controller.list(USER, null, "mine", 200).block();

        assertThat(response).isNotNull();
        assertThat(response.getBody()).hasSize(1);
    }

    @Test
    @DisplayName("scope=all merges the approver queue with the caller's own requests without duplicates")
    void listScopesMergeWithoutDuplicates() {
        ApprovalTask mine = task("pending", "queued");
        approvalService.decisionTask = mine;
        approvalService.queue = List.of(mine);

        ResponseEntity<List<ApprovalTask>> all =
                controller.list(USER, "network_admin", "all", 200).block();

        assertThat(all).isNotNull();
        assertThat(all.getBody()).hasSize(1);
    }

    // ------------------------------------------------------------------ separation of duties

    @Test
    @DisplayName("a self-approval attempt is 403 SELF_APPROVAL_FORBIDDEN and the status is untouched")
    void selfApprovalIsForbidden() {
        decideRefused(task("pending", "queued"), ApprovalService.Failure.SELF_APPROVAL);

        ResponseEntity<Object> response = decide(Map.of("action", "approve"));

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(403);
        assertThat(body(response)).containsEntry("error", "SELF_APPROVAL_FORBIDDEN");
        assertThat(resumeClient.calls).isEmpty();
        assertThat(approvalService.resumeSucceeded).isEmpty();
        assertThat(auditService.rows).hasSize(1);
        assertThat(auditService.rows.get(0).outcome()).isEqualTo(AuditService.OUTCOME_DENIED);
        assertThat(auditService.rows.get(0).detail().get("reason"))
                .isEqualTo("SELF_APPROVAL_FORBIDDEN");
    }

    @Test
    @DisplayName("a decider lacking every required role is 403 APPROVER_ROLE_REQUIRED")
    void missingRoleIsForbidden() {
        decideRefused(task("pending", "queued"), ApprovalService.Failure.ROLE_REQUIRED);

        ResponseEntity<Object> response = controller
                .decide(USER, "employee", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(403);
        assertThat(body(response)).containsEntry("error", "APPROVER_ROLE_REQUIRED");
        assertThat(resumeClient.calls).isEmpty();
        assertThat(auditService.rows).hasSize(1);
        assertThat(auditService.rows.get(0).detail().get("reason"))
                .isEqualTo("APPROVER_ROLE_REQUIRED");
    }

    @Test
    @DisplayName("the satisfied decider's action is audited as a success with the acting roles")
    void successfulDecisionIsAudited() {
        decideReturns(task("approved", "succeeded"), true, false);

        controller.decide(USER, "approver,employee", APPROVAL, Map.of("action", "approve"), null).block();

        assertThat(auditService.rows).hasSize(1);
        var row = auditService.rows.get(0);
        assertThat(row.action()).isEqualTo("approval.decide");
        assertThat(row.actorId()).isEqualTo(USER);
        assertThat(row.objectId()).isEqualTo(APPROVAL);
        assertThat(row.outcome()).isEqualTo(AuditService.OUTCOME_SUCCESS);
        assertThat(row.detail().get("action")).isEqualTo("approve");
    }

    @Test
    @DisplayName("the optional comment travels with the decision")
    void commentIsForwarded() {
        decideReturns(task("approved", "succeeded"), true, false);

        controller.decide(
                USER,
                "approver",
                APPROVAL,
                Map.of("action", "approve", "comment", "  reviewed the invoice  "),
                null
        ).block();

        assertThat(approvalService.lastComment).isEqualTo("reviewed the invoice");
    }

    // ------------------------------------------------------------------ full SoD path

    /**
     * Runs the controller against a fake that applies the same authorisation rules as
     * {@code ApprovalService}, so the whole decide path is exercised end to end.
     */
    private void pendingApprovalRequiring(String requiredRoles) {
        ApprovalTask task = task("pending", "queued");
        task.setRequiredApproverRoles(requiredRoles);
        approvalService.decisionTask = task;
        approvalService.applyPolicy = true;
        approvalService.newlyDecided = true;
    }

    @Test
    @DisplayName("the requester is refused: 403 SELF_APPROVAL_FORBIDDEN and the agent is never called")
    void requesterIsRefusedOnTheFullPath() {
        pendingApprovalRequiring("approver");

        // USER is the requester of the task built by task(...).
        ResponseEntity<Object> response = controller
                .decide(USER, "approver", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(403);
        assertThat(body(response)).containsEntry("error", "SELF_APPROVAL_FORBIDDEN");
        assertThat(resumeClient.calls).isEmpty();
        assertThat(approvalService.resumeSucceeded).isEmpty();
    }

    @Test
    @DisplayName("a *different* user holding the required role can decide")
    void differentApproverWithTheRoleCanDecide() {
        pendingApprovalRequiring("approver");

        ResponseEntity<Object> response = controller
                .decide("user-approver", "approver,employee", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        // The decision reached the agent, i.e. it really was applied.
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":approved:" + RESUME_REQUEST_ID);
        assertThat(approvalService.resumeSucceeded).containsExactly(APPROVAL);
    }

    @Test
    @DisplayName("a different user WITHOUT the required role is 403 APPROVER_ROLE_REQUIRED")
    void differentApproverWithoutTheRoleIsRefused() {
        pendingApprovalRequiring("finance-approver");

        ResponseEntity<Object> response = controller
                .decide("user-approver", "employee", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(403);
        assertThat(body(response)).containsEntry("error", "APPROVER_ROLE_REQUIRED");
        assertThat(resumeClient.calls).isEmpty();
    }

    @Test
    @DisplayName("the literal admin role passes the gate on the full path")
    void adminPassesOnTheFullPath() {
        pendingApprovalRequiring("finance-approver");

        ResponseEntity<Object> response = controller
                .decide("user-admin", "admin", APPROVAL, Map.of("action", "approve"), null)
                .block();

        assertThat(response).isNotNull();
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(resumeClient.calls)
                .containsExactly(APPROVAL + ":approved:" + RESUME_REQUEST_ID);
    }
}
