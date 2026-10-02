package com.skychat.service;

import com.skychat.domain.ApprovalTask;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Retries approved-but-unresumed approvals against the agent service.
 *
 * <p>Every resume uses the {@code resume_request_id} persisted when the decision was claimed,
 * so a retry after a crash is deduplicated by the agent. A 409 or 404 parks the approval in
 * the terminal {@code stale} state and it is never retried again.</p>
 */
@Service
public class ApprovalResumeReconciler {
    private static final Logger log = LoggerFactory.getLogger(ApprovalResumeReconciler.class);
    private static final int BATCH_SIZE = 20;

    private final ApprovalService approvalService;
    private final WorkflowResumeClient workflowResumeClient;
    private final ApprovalResumeGuard resumeGuard;

    /**
     * Guards against overlapping ticks when a batch takes longer than the fixed delay.
     */
    private final AtomicBoolean ticking = new AtomicBoolean(false);

    public ApprovalResumeReconciler(
            ApprovalService approvalService,
            WorkflowResumeClient workflowResumeClient,
            ApprovalResumeGuard resumeGuard
    ) {
        this.approvalService = approvalService;
        this.workflowResumeClient = workflowResumeClient;
        this.resumeGuard = resumeGuard;
    }

    @Scheduled(
            fixedDelayString = "${skychat.approval.resume-retry-ms:15000}",
            initialDelayString = "${skychat.approval.resume-retry-initial-ms:20000}"
    )
    public void reconcile() {
        if (!ticking.compareAndSet(false, true)) {
            log.debug("Skipping approval resume tick: previous tick is still running");
            return;
        }
        try {
            List<ApprovalTask> candidates = approvalService.resumeCandidates(BATCH_SIZE);
            if (candidates.isEmpty()) {
                return;
            }
            log.debug("Resuming {} pending approval(s)", candidates.size());
            for (ApprovalTask task : candidates) {
                reconcileOne(task);
            }
        } catch (Exception error) {
            log.warn("Approval resume tick failed: {}", error.toString());
        } finally {
            ticking.set(false);
        }
    }

    private void reconcileOne(ApprovalTask task) {
        if (!resumeGuard.tryAcquire(task.getId())) {
            log.debug("Approval {} is already being resumed by another caller", task.getId());
            return;
        }
        try {
            // Both outcomes are delivered: a rejection must consume the interrupt so
            // the run terminates instead of staying resumable forever.
            String decision = "rejected".equals(task.getStatus()) ? "rejected" : "approved";
            WorkflowResumeClient.ResumeResult result = workflowResumeClient
                    .resume(task, decision, task.getResumeRequestId())
                    .block();

            if (result == null) {
                approvalService.markResumeFailed(task.getId(), "EMPTY_RESUME_RESULT");
                return;
            }

            switch (result.outcome()) {
                case SUCCEEDED -> {
                    approvalService.markResumeSucceeded(task.getId());
                    log.info("Approval {} resumed ({})", task.getId(), result.status());
                }
                case STALE -> {
                    approvalService.markResumeStale(task.getId(), result.error());
                    log.warn("Approval {} is stale: {}", task.getId(), result.error());
                }
                case NOT_FOUND -> {
                    approvalService.markResumeStale(task.getId(), "APPROVAL_NOT_FOUND");
                    log.warn("Approval {} is unknown to the agent service", task.getId());
                }
                case TRANSIENT_FAILURE -> {
                    approvalService.markResumeFailed(task.getId(), result.error());
                    log.warn("Approval {} resume failed, will retry: {}",
                            task.getId(), result.error());
                }
            }
        } catch (Exception error) {
            log.warn("Approval {} resume attempt crashed: {}", task.getId(), error.toString());
        } finally {
            resumeGuard.release(task.getId());
        }
    }
}
