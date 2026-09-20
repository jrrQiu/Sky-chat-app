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
        SELECT id, run_id, user_id, agent_id, intent, risk_level, rule_id, status, created_at, updated_at
        FROM approval_task
        WHERE user_id = #{userId}
        ORDER BY updated_at DESC
    """)
    List<ApprovalTask> findByUserId(String userId);

    @Select("""
        SELECT id, run_id, user_id, agent_id, intent, risk_level, rule_id, status, created_at, updated_at
        FROM approval_task
        WHERE id = #{id} AND user_id = #{userId}
    """)
    ApprovalTask findById(@Param("id") String id, @Param("userId") String userId);

    @Insert("""
        INSERT INTO approval_task (
            id, run_id, user_id, agent_id, intent, risk_level, rule_id, status, created_at, updated_at
        ) VALUES (
            #{id}, #{runId}, #{userId}, #{agentId}, #{intent}, #{riskLevel}, #{ruleId}, #{status}, #{createdAt}, #{updatedAt}
        )
    """)
    int insert(ApprovalTask task);

    @Update("""
        UPDATE approval_task
        SET status = #{status}, updated_at = #{updatedAt}
        WHERE id = #{id} AND user_id = #{userId} AND status = 'pending'
    """)
    int updateStatus(
            @Param("id") String id,
            @Param("userId") String userId,
            @Param("status") String status,
            @Param("updatedAt") java.time.LocalDateTime updatedAt
    );
}
