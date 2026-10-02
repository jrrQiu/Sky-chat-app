package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.ApprovalDecision;
import com.skychat.domain.ApprovalTask;
import com.skychat.service.ApprovalResumeGuard;
import com.skychat.service.ApprovalService;
import com.skychat.service.AuditService;
import com.skychat.service.WorkflowResumeClient;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/v1/approvals")
public class ApprovalController {
    private final ApprovalService approvalService;
    private final WorkflowResumeClient workflowResumeClient;
    private final ApprovalResumeGuard resumeGuard;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public ApprovalController(
            ApprovalService approvalService,
            WorkflowResumeClient workflowResumeClient,
            ApprovalResumeGuard resumeGuard,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.approvalService = approvalService;
        this.workflowResumeClient = workflowResumeClient;
        this.resumeGuard = resumeGuard;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    /**
     * Either a terminal response, or a decision that still owes the agent a resume call.
     */
    private record DecisionStage(ResponseEntity<Object> response, ApprovalTask resumeTask) {
        static DecisionStage immediate(ResponseEntity<Object> response) {
            return new DecisionStage(response, null);
        }

        static DecisionStage resume(ApprovalTask task) {
            return new DecisionStage(null, task);
        }
    }

    /**
     * Lists approvals for the approvals workspace.
     *
     * <p>{@code scope} selects the view: {@code mine} (default) is the caller's own requests,
     * {@code to_approve} is the pending queue this caller is entitled to decide, and {@code all}
     * merges both. The queue applies the same role rules as the decision endpoint, so the UI never
     * offers an action the server would refuse.</p>
     */
    @GetMapping
    public Mono<ResponseEntity<List<ApprovalTask>>> list(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestParam(value = "scope", defaultValue = "mine") String scope,
            @RequestParam(value = "limit", defaultValue = "200") int limit
    ) {
        List<String> roles = parseRoles(rolesHeader);
        int boundedLimit = Math.max(1, Math.min(limit, 500));
        return Mono.fromCallable(() -> switch (scope) {
                    case "to_approve" -> approvalService.listToApprove(userId, roles, boundedLimit);
                    case "all" -> approvalService.listAll(userId, roles, boundedLimit);
                    default -> approvalService.list(userId);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    /**
     * The append-only decision trail for one approval, so the workspace can show how a decision
     * was reached rather than only what the current status is.
     */
    @GetMapping("/{id}/decisions")
    public Mono<ResponseEntity<List<ApprovalDecision>>> decisions(
            @PathVariable String id
    ) {
        return Mono.fromCallable(() -> approvalService.decisions(id))
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    @PatchMapping("/{id}/decision")
    public Mono<ResponseEntity<Object>> decide(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, String> body,
            ServerWebExchange exchange
    ) {
        List<String> roles = parseRoles(rolesHeader);
        String sourceIp = sourceIp(exchange);
        return Mono.fromCallable(() -> claim(userId, id, body, roles, sourceIp))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(stage -> stage.response() != null
                        ? Mono.just(stage.response())
                        : resume(stage.resumeTask()));
    }

    private DecisionStage claim(
            String userId,
            String id,
            Map<String, String> body,
            List<String> roles,
            String sourceIp
    ) {
        String action = body.getOrDefault("action", "");
        if (!"approve".equals(action) && !"reject".equals(action)) {
            return DecisionStage.immediate(ResponseEntity.<Object>badRequest()
                    .body(Map.of("error", "invalid action")));
        }

        String comment = commentOf(body);
        ApprovalService.Decision decision = approvalService.decide(
                userId,
                id,
                action,
                comment,
                roles,
                sourceIp
        );
        ApprovalTask task = decision.task();

        if (decision.failure() != null) {
            return DecisionStage.immediate(refusal(decision, userId, id, roles, sourceIp));
        }

        if (task == null) {
            auditDecision(userId, id, roles, sourceIp, action, AuditService.OUTCOME_FAILURE, "APPROVAL_NOT_FOUND");
            return DecisionStage.immediate(ResponseEntity.<Object>status(404)
                    .body(Map.of("error", "APPROVAL_NOT_FOUND")));
        }

        String status = task.getStatus();
        if (!"approved".equals(status) && !"rejected".equals(status)) {
            // The compare-and-set lost to a concurrent request that has not finished writing;
            // the caller can simply re-read the approval.
            return DecisionStage.immediate(ResponseEntity.<Object>ok(task));
        }

        // A repeated click asking for the opposite outcome is a conflict, not a
        // retry: the approval is already final and the agent already knows.
        String requestedStatus = "approve".equals(action) ? "approved" : "rejected";
        if (decision.alreadyDecided() && !requestedStatus.equals(status)) {
            auditDecision(userId, id, roles, sourceIp, action, AuditService.OUTCOME_DENIED, "APPROVAL_ALREADY_DECIDED");
            return DecisionStage.immediate(ResponseEntity.<Object>status(409).body(Map.of(
                    "error", "APPROVAL_ALREADY_DECIDED:" + status,
                    "approval", task
            )));
        }

        if (decision.newlyDecided()) {
            auditDecision(userId, id, roles, sourceIp, action, AuditService.OUTCOME_SUCCESS, null);
        }
        // Both outcomes are delivered to the agent. A rejection must consume the
        // interrupt too: leaving the checkpoint pending would strand the run and
        // leave the approval resumable as "approved" through the agent's own
        // resume endpoint.
        String resumeState = task.getResumeState();
        boolean resumable = "queued".equals(resumeState) || "failed".equals(resumeState);
        if (!resumable) {
            // Already delivered (succeeded), permanently unusable (stale), or a
            // duplicate click while the first attempt is still in flight.
            return DecisionStage.immediate(ResponseEntity.<Object>ok(task));
        }
        // A repeated click that never reached the agent re-enters here; the
        // persisted resume_request_id keeps the delivery idempotent.
        return DecisionStage.resume(task);
    }

    /**
     * Maps a refused decision onto its HTTP contract: separation of duties and the required
     * approver roles are both 403, an unknown approval is 404.
     */
    private ResponseEntity<Object> refusal(
            ApprovalService.Decision decision,
            String userId,
            String id,
            List<String> roles,
            String sourceIp
    ) {
        return switch (decision.failure()) {
            case SELF_APPROVAL -> {
                auditDecision(userId, id, roles, sourceIp, null, AuditService.OUTCOME_DENIED, "SELF_APPROVAL_FORBIDDEN");
                yield ResponseEntity.<Object>status(HttpStatus.FORBIDDEN)
                        .body(Map.of("error", "SELF_APPROVAL_FORBIDDEN"));
            }
            case ROLE_REQUIRED -> {
                auditDecision(userId, id, roles, sourceIp, null, AuditService.OUTCOME_DENIED, "APPROVER_ROLE_REQUIRED");
                yield ResponseEntity.<Object>status(HttpStatus.FORBIDDEN)
                        .body(Map.of("error", "APPROVER_ROLE_REQUIRED"));
            }
            case NOT_FOUND -> {
                auditDecision(userId, id, roles, sourceIp, null, AuditService.OUTCOME_FAILURE, "APPROVAL_NOT_FOUND");
                yield ResponseEntity.<Object>status(404).body(Map.of("error", "APPROVAL_NOT_FOUND"));
            }
        };
    }

    private void auditDecision(
            String userId,
            String approvalId,
            List<String> roles,
            String sourceIp,
            String action,
            String outcome,
            String reason
    ) {
        Map<String, Object> detail = new LinkedHashMap<>();
        if (action != null) {
            detail.put("action", action);
        }
        detail.put("roles", roles);
        if (reason != null) {
            detail.put("reason", reason);
        }
        auditService.record(
                AuditService.Context.user(userId, sourceIp, null),
                "approval.decide",
                "approval",
                approvalId,
                outcome,
                detail
        );
    }

    private String commentOf(Map<String, String> body) {
        String comment = body.get("comment");
        if (comment == null) {
            return null;
        }
        String trimmed = comment.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    /**
     * The exchange is optional so the decision path stays unit-testable without one; the
     * resolver returns null rather than a shared placeholder address.
     */
    private String sourceIp(ServerWebExchange exchange) {
        return exchange == null
                ? null
                : clientAddressResolver.resolve(exchange.getRequest());
    }

    private List<String> parseRoles(String rolesHeader) {
        if (rolesHeader == null || rolesHeader.isBlank()) {
            return List.of();
        }
        return Arrays.stream(rolesHeader.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .toList();
    }

    private Mono<ResponseEntity<Object>> resume(ApprovalTask task) {
        if (!resumeGuard.tryAcquire(task.getId())) {
            // Another resume for this approval is in flight; let the caller retry later
            // instead of blocking the request thread.
            return Mono.just(ResponseEntity.<Object>accepted().body(Map.of(
                    "approval", task,
                    "resume", Map.of(
                            "resumed", false,
                            "retryable", true,
                            "error", "RESUME_IN_PROGRESS"
                    )
            )));
        }

        String decision = "rejected".equals(task.getStatus()) ? "rejected" : "approved";
        return workflowResumeClient.resume(task, decision, task.getResumeRequestId())
                .flatMap(result -> Mono.fromCallable(() -> apply(task, result))
                        .subscribeOn(Schedulers.boundedElastic()))
                .doFinally(signal -> resumeGuard.release(task.getId()));
    }

    private ResponseEntity<Object> apply(
            ApprovalTask task,
            WorkflowResumeClient.ResumeResult result
    ) {
        if (result.succeeded()) {
            approvalService.markResumeSucceeded(task.getId());
            Map<String, Object> resume = new LinkedHashMap<>();
            resume.put("resumed", true);
            resume.put("status", result.status());
            return ResponseEntity.<Object>ok(Map.of(
                    "approval", reload(task),
                    "resume", resume
            ));
        }

        if (result.stale() || result.notFound()) {
            // Gone or stale checkpoint: never retry automatically and never start a new run.
            String error = result.notFound() ? "APPROVAL_NOT_FOUND" : result.error();
            approvalService.markResumeStale(task.getId(), error);
            return ResponseEntity.<Object>status(409).body(Map.of(
                    "error", error == null ? "STALE_APPROVAL" : error,
                    "approval", reload(task)
            ));
        }

        approvalService.markResumeFailed(task.getId(), result.error());
        Map<String, Object> resume = new LinkedHashMap<>();
        resume.put("resumed", false);
        resume.put("retryable", true);
        resume.put("error", result.error());
        return ResponseEntity.<Object>accepted().body(Map.of(
                "approval", reload(task),
                "resume", resume
        ));
    }

    private ApprovalTask reload(ApprovalTask task) {
        ApprovalTask reloaded = approvalService.findByIdInternal(task.getId());
        return reloaded == null ? task : reloaded;
    }
}
