package com.skychat.service;

import com.skychat.domain.ApprovalDecision;
import com.skychat.domain.ApprovalTask;
import com.skychat.mapper.ApprovalDecisionMapper;
import com.skychat.mapper.ApprovalMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Owns the durable state of an approval: who may decide it, what the decision was, and the
 * resume that has to follow.
 */
@Service
public class ApprovalService {
    /**
     * The literal role that always passes a required-role gate: administrators must stay able
     * to unblock a stranded approval.
     */
    private static final String ADMIN_ROLE = "admin";

    private final ApprovalMapper approvalMapper;
    private final ApprovalDecisionMapper approvalDecisionMapper;

    public ApprovalService(
            ApprovalMapper approvalMapper,
            ApprovalDecisionMapper approvalDecisionMapper
    ) {
        this.approvalMapper = approvalMapper;
        this.approvalDecisionMapper = approvalDecisionMapper;
    }

    /**
     * Why a decision was refused before touching the row.
     */
    public enum Failure {
        /**
         * The decider is the requester; separation of duties forbids self-approval.
         */
        SELF_APPROVAL,
        /**
         * The decider's roles do not intersect {@code required_approver_roles}.
         */
        ROLE_REQUIRED,
        /**
         * The approval does not exist or is not owned by the decider.
         */
        NOT_FOUND
    }

    /**
     * Outcome of a decision attempt.
     *
     * <p>{@code newlyDecided} means this call won the compare-and-set claim and owns the
     * freshly generated {@code resumeRequestId}. {@code alreadyDecided} means the row was
     * already decided before this call (typically a double click or a retry after a Java
     * crash); the task carries the persisted {@code resumeRequestId} so a retry reuses the
     * SAME id and the agent service can deduplicate it. A null task with all flags false and
     * a non-null {@code failure} means the decision was refused by a server-side rule.</p>
     */
    public record Decision(
            ApprovalTask task,
            boolean newlyDecided,
            boolean alreadyDecided,
            Failure failure
    ) {
        public static Decision allowed(ApprovalTask task, boolean newlyDecided, boolean alreadyDecided) {
            return new Decision(task, newlyDecided, alreadyDecided, null);
        }

        public static Decision refused(ApprovalTask task, Failure failure) {
            return new Decision(task, false, false, failure);
        }
    }

    public List<ApprovalTask> list(String userId) {
        // Scoped to the caller's own requests (the "my requests" view).
        return approvalMapper.findByUserId(userId);
    }

    /**
     * The approver queue: pending approvals this caller may decide.
     *
     * <p>The role test is applied in SQL with the same semantics as {@link #decide}: a row with no
     * required roles is decidable by any non-requester, {@code admin} bypasses the test, and the
     * requester is always excluded. Keeping the two in step matters — a queue that shows an
     * approval the decision endpoint then refuses is worse than no queue at all.</p>
     */
    public List<ApprovalTask> listToApprove(
            String userId,
            List<String> roles,
            int limit
    ) {
        boolean isAdmin = roles != null && roles.contains(ADMIN_ROLE);
        return approvalMapper.findPendingForApprover(
                userId,
                String.join(",", roles == null ? List.of() : roles),
                isAdmin,
                limit
        );
    }

    /**
     * Everything the caller can act on: their own requests plus the approver queue, deduplicated
     * and newest first. This is what the approvals workspace shows by default.
     */
    public List<ApprovalTask> listAll(String userId, List<String> roles, int limit) {
        Map<String, ApprovalTask> merged = new LinkedHashMap<>();
        for (ApprovalTask task : listToApprove(userId, roles, limit)) {
            merged.put(task.getId(), task);
        }
        for (ApprovalTask task : list(userId)) {
            merged.putIfAbsent(task.getId(), task);
        }
        return merged.values().stream()
                .sorted(Comparator.comparing(
                        ApprovalTask::getCreatedAt,
                        Comparator.nullsLast(Comparator.reverseOrder())
                ))
                .limit(limit)
                .toList();
    }

    /**
     * Applies a decision after enforcing separation of duties and the required approver roles.
     *
     * <p>Both rules are checked against the persisted row before the compare-and-set, so a
     * refused decision leaves the status completely untouched.</p>
     */
    public Decision decide(
            String userId,
            String id,
            String action,
            String comment,
            List<String> deciderRoles,
            String sourceIp
    ) {
        String status = "approve".equals(action) ? "approved" : "rejected";
        String resumeRequestId = "rsq_" + UUID.randomUUID();
        String resumedExecutionId = "exec_" + UUID.randomUUID();
        LocalDateTime now = LocalDateTime.now();

        // Authorisation first: resolve the approval without the ownership filter so a
        // role-qualified approver can act on somebody else's high-risk request.
        ApprovalTask existing = approvalMapper.findByIdInternal(id);

        if (existing == null) {
            return Decision.refused(null, Failure.NOT_FOUND);
        }
        if (userId != null && userId.equals(existing.getUserId())) {
            return Decision.refused(existing, Failure.SELF_APPROVAL);
        }
        if (!hasRequiredRole(existing.getRequiredApproverRoles(), deciderRoles)) {
            return Decision.refused(existing, Failure.ROLE_REQUIRED);
        }

        // The compare-and-set is a single-winner claim on status='pending' only: user_id is the
        // requester, so scoping it by the decider would make every approval undecidable.
        if (approvalMapper.updateStatus(
                id,
                status,
                resumeRequestId,
                resumedExecutionId,
                now,
                userId,
                now,
                comment,
                now
        ) == 1) {
            ApprovalTask decided = approvalMapper.findByIdInternal(id);
            recordDecision(decided, action, userId, deciderRoles, comment, sourceIp);
            return Decision.allowed(decided, true, false);
        }

        // The compare-and-set lost: either somebody else won the race, or the row was
        // already decided. Re-read and report the persisted state.
        ApprovalTask current = approvalMapper.findByIdInternal(id);
        if (current == null) {
            return Decision.refused(null, Failure.NOT_FOUND);
        }
        return Decision.allowed(current, false, true);
    }

    /**
     * Appends one immutable trail row. The approval row keeps only the latest state, so this
     * is the durable answer to "who approved what, and when".
     */
    private void recordDecision(
            ApprovalTask task,
            String action,
            String userId,
            List<String> deciderRoles,
            String comment,
            String sourceIp
    ) {
        ApprovalDecision decision = new ApprovalDecision();
        decision.setId(UUID.randomUUID().toString());
        decision.setApprovalId(task == null ? null : task.getId());
        decision.setDecision("approve".equals(action) ? "approved" : "rejected");
        decision.setDecidedBy(userId);
        decision.setDecidedByRoles(joinRoles(deciderRoles));
        decision.setComment(comment);
        decision.setSourceIp(sourceIp);
        decision.setResumeRequestId(task == null ? null : task.getResumeRequestId());
        decision.setCreatedAt(LocalDateTime.now());
        approvalDecisionMapper.insert(decision);
    }

    public List<ApprovalDecision> decisions(String approvalId) {
        return approvalDecisionMapper.findByApprovalId(approvalId);
    }

    /**
     * The required-role gate. An empty requirement is open to any non-requester, and the
     * literal {@code admin} role always passes so a stranded approval can never become
     * undecidable.
     */
    public static boolean hasRequiredRole(String requiredApproverRoles, List<String> deciderRoles) {
        Set<String> required = parseRoles(requiredApproverRoles);
        if (required.isEmpty()) {
            return true;
        }
        Set<String> actual = parseRoles(joinRoles(deciderRoles));
        if (actual.contains(ADMIN_ROLE)) {
            return true;
        }
        return required.stream().anyMatch(actual::contains);
    }

    private static Set<String> parseRoles(String csv) {
        if (csv == null || csv.isBlank()) {
            return Set.of();
        }
        return Arrays.stream(csv.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .map(value -> value.toLowerCase(Locale.ROOT))
                .collect(Collectors.toCollection(LinkedHashSet::new));
    }

    private static String joinRoles(List<String> roles) {
        if (roles == null || roles.isEmpty()) {
            return "";
        }
        return roles.stream()
                .filter(role -> role != null && !role.isBlank())
                .map(String::trim)
                .collect(Collectors.joining(","));
    }

    public ApprovalTask findById(String id, String userId) {
        return approvalMapper.findById(id, userId);
    }

    public ApprovalTask findByIdInternal(String id) {
        return approvalMapper.findByIdInternal(id);
    }

    public List<ApprovalTask> resumeCandidates(int limit) {
        return approvalMapper.findResumeCandidates(limit);
    }

    public void markResumeSucceeded(String id) {
        LocalDateTime now = LocalDateTime.now();
        approvalMapper.markResumeResult(id, "succeeded", null, now, now);
    }

    public void markResumeFailed(String id, String error) {
        approvalMapper.markResumeResult(id, "failed", error, null, LocalDateTime.now());
    }

    public void markResumeStale(String id, String error) {
        approvalMapper.markResumeResult(id, "stale", error, null, LocalDateTime.now());
    }
}
