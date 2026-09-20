package com.skychat.service;

import com.skychat.domain.ApprovalTask;
import com.skychat.mapper.ApprovalMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class ApprovalService {
    private final ApprovalMapper approvalMapper;

    public ApprovalService(ApprovalMapper approvalMapper) {
        this.approvalMapper = approvalMapper;
    }

    public List<ApprovalTask> list(String userId) {
        return approvalMapper.findByUserId(userId);
    }

    public ApprovalTask decide(String userId, String id, String action) {
        String status = "approve".equals(action) ? "approved" : "rejected";
        if (approvalMapper.updateStatus(id, userId, status, LocalDateTime.now()) == 0) {
            return null;
        }
        return approvalMapper.findById(id, userId);
    }
}
