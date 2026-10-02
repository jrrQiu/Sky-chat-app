package com.skychat.mapper;

import com.skychat.domain.ApprovalDecision;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface ApprovalDecisionMapper {
    @Insert("""
        INSERT INTO approval_decision (
            id, approval_id, decision, decided_by, decided_by_roles, comment,
            source_ip, resume_request_id, created_at
        ) VALUES (
            #{id}, #{approvalId}, #{decision}, #{decidedBy}, #{decidedByRoles}, #{comment},
            #{sourceIp}, #{resumeRequestId}, #{createdAt}
        )
    """)
    int insert(ApprovalDecision decision);

    @Select("""
        SELECT id, approval_id, decision, decided_by, decided_by_roles, comment,
               source_ip, resume_request_id, created_at
        FROM approval_decision
        WHERE approval_id = #{approvalId}
        ORDER BY created_at ASC, id ASC
    """)
    List<ApprovalDecision> findByApprovalId(@Param("approvalId") String approvalId);
}
