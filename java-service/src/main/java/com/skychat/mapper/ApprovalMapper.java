package com.skychat.mapper;

import com.skychat.domain.ApprovalTask;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

@Mapper
public interface ApprovalMapper {
    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE user_id = #{userId}
        ORDER BY updated_at DESC
    """)
    List<ApprovalTask> findByUserId(String userId);

    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE id = #{id} AND user_id = #{userId}
    """)
    ApprovalTask findById(@Param("id") String id, @Param("userId") String userId);

    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE id = #{id}
    """)
    ApprovalTask findByIdInternal(@Param("id") String id);

    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE user_id = #{userId}
          AND status = 'pending'
        ORDER BY updated_at DESC
        LIMIT 1
    """)
    ApprovalTask findLatestPending(String userId);

    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE status IN ('approved', 'rejected')
          AND resume_state IN ('queued', 'failed')
          AND resume_attempts < 5
          AND (checkpoint_id IS NOT NULL OR interrupt_id IS NOT NULL)
        ORDER BY updated_at ASC
        LIMIT #{limit}
    """)
    List<ApprovalTask> findResumeCandidates(@Param("limit") int limit);

    /**
     * The approver queue: pending approvals the caller did not raise and is entitled to decide.
     *
     * <p>Excludes the requester (separation of duties) and keeps only rows whose
     * {@code required_approver_roles} intersects the caller's roles; a row with no required roles
     * is decidable by any non-requester. {@code isAdmin} bypasses the role test, matching the
     * service-side authorisation so the queue never shows something the decision would refuse.</p>
     */
    @Select("""
        SELECT id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
               approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
               consumed_at, user_id, agent_id, intent, risk_level, rule_id,
               required_approver_roles, resume_token, resumed_execution_id, status,
               decided_by, decided_at, decision_comment,
               created_at, updated_at, resumed_at
        FROM approval_task
        WHERE status = 'pending'
          AND user_id <> #{userId}
          AND (
              #{isAdmin} = TRUE
              OR required_approver_roles IS NULL
              OR required_approver_roles = ''
              OR string_to_array(required_approver_roles, ',')
                 && string_to_array(#{roles}, ',')
          )
        ORDER BY created_at DESC
        LIMIT #{limit}
    """)
    List<ApprovalTask> findPendingForApprover(
            @Param("userId") String userId,
            @Param("roles") String roles,
            @Param("isAdmin") boolean isAdmin,
            @Param("limit") int limit
    );

    @Insert("""
        INSERT INTO approval_task (
            id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
            approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
            consumed_at, user_id, agent_id, intent, risk_level, rule_id,
            required_approver_roles, resume_token, resumed_execution_id, status,
            decided_by, decided_at, decision_comment,
            created_at, updated_at, resumed_at
        ) VALUES (
            #{id}, #{runId}, #{turnId}, #{threadId}, #{checkpointId}, #{checkpointNs}, #{interruptId},
            #{approvalKey}, #{resumeRequestId}, COALESCE(#{resumeState}, 'queued'),
            COALESCE(#{resumeAttempts}, 0), #{resumeError},
            #{consumedAt}, #{userId}, #{agentId}, #{intent}, #{riskLevel}, #{ruleId},
            #{requiredApproverRoles}, #{resumeToken}, #{resumedExecutionId}, #{status},
            #{decidedBy}, #{decidedAt}, #{decisionComment},
            #{createdAt}, #{updatedAt}, #{resumedAt}
        )
    """)
    int insert(ApprovalTask task);

    @Insert("""
        INSERT INTO approval_task (
            id, run_id, turn_id, thread_id, checkpoint_id, checkpoint_ns, interrupt_id,
            approval_key, resume_request_id, resume_state, resume_attempts, resume_error,
            consumed_at, user_id, agent_id, intent, risk_level, rule_id,
            required_approver_roles, resume_token, resumed_execution_id, status,
            decided_by, decided_at, decision_comment,
            created_at, updated_at, resumed_at
        ) VALUES (
            #{id}, #{runId}, #{turnId}, #{threadId}, #{checkpointId}, #{checkpointNs}, #{interruptId},
            #{approvalKey}, #{resumeRequestId}, COALESCE(#{resumeState}, 'queued'),
            COALESCE(#{resumeAttempts}, 0), #{resumeError},
            #{consumedAt}, #{userId}, #{agentId}, #{intent}, #{riskLevel}, #{ruleId},
            #{requiredApproverRoles}, #{resumeToken}, #{resumedExecutionId}, #{status},
            #{decidedBy}, #{decidedAt}, #{decisionComment},
            #{createdAt}, #{updatedAt}, #{resumedAt}
        )
        ON CONFLICT (id) DO UPDATE SET
            checkpoint_id = EXCLUDED.checkpoint_id,
            checkpoint_ns = EXCLUDED.checkpoint_ns,
            interrupt_id = EXCLUDED.interrupt_id,
            approval_key = COALESCE(approval_task.approval_key, EXCLUDED.approval_key),
            turn_id = COALESCE(approval_task.turn_id, EXCLUDED.turn_id),
            thread_id = COALESCE(approval_task.thread_id, EXCLUDED.thread_id),
            -- The agent may attach the required approver roles on a later re-emit of the
            -- same approval; never overwrite an already recorded requirement with NULL.
            required_approver_roles = COALESCE(
                EXCLUDED.required_approver_roles,
                approval_task.required_approver_roles
            ),
            updated_at = EXCLUDED.updated_at
    """)
    int upsertFromEvent(ApprovalTask task);

    /**
     * Single-winner claim on a pending approval.
     *
     * <p>The predicate is deliberately {@code id + status = 'pending'} only.
     * {@code approval_task.user_id} is the <em>requester</em>, not the decider, so scoping this
     * update by it would let only the requester claim the row - exactly the user separation of
     * duties has to reject. Authorisation (self-approval and required roles) happens in
     * {@code ApprovalService#decide} before this CAS runs.</p>
     */
    @Update("""
        UPDATE approval_task
        SET status = #{status},
            resume_request_id = #{resumeRequestId},
            resume_state = 'queued',
            resume_attempts = 0,
            resume_error = NULL,
            resumed_execution_id = #{resumedExecutionId},
            resumed_at = #{resumedAt},
            decided_by = #{decidedBy},
            decided_at = #{decidedAt},
            decision_comment = #{decisionComment},
            updated_at = #{updatedAt}
        WHERE id = #{id} AND status = 'pending'
    """)
    int updateStatus(
            @Param("id") String id,
            @Param("status") String status,
            @Param("resumeRequestId") String resumeRequestId,
            @Param("resumedExecutionId") String resumedExecutionId,
            @Param("resumedAt") java.time.LocalDateTime resumedAt,
            @Param("decidedBy") String decidedBy,
            @Param("decidedAt") java.time.LocalDateTime decidedAt,
            @Param("decisionComment") String decisionComment,
            @Param("updatedAt") java.time.LocalDateTime updatedAt
    );

    @Update("""
        UPDATE approval_task
        SET resume_state = #{resumeState},
            resume_error = #{resumeError},
            consumed_at = COALESCE(#{consumedAt}, consumed_at),
            resume_attempts = resume_attempts + 1,
            updated_at = #{updatedAt}
        WHERE id = #{id}
    """)
    int markResumeResult(
            @Param("id") String id,
            @Param("resumeState") String resumeState,
            @Param("resumeError") String resumeError,
            @Param("consumedAt") java.time.LocalDateTime consumedAt,
            @Param("updatedAt") java.time.LocalDateTime updatedAt
    );
}
