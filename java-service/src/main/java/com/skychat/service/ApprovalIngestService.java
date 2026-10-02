package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ApprovalTask;
import com.skychat.mapper.ApprovalMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.util.HexFormat;

/**
 * Persists {@code approval_required} events emitted by the agent service while a chat run
 * streams, so the Java service owns the durable record of every high-risk approval.
 *
 * <p>Every failure path is swallowed and logged: a malformed or unexpected event must never
 * break the chat stream.</p>
 */
@Service
public class ApprovalIngestService {
    private static final Logger log = LoggerFactory.getLogger(ApprovalIngestService.class);

    private static final String EVENT_TYPE = "approval_required";
    private static final String DEFAULT_AGENT_ID = "approval";
    private static final String DEFAULT_RULE_ID = "HIGH-RISK-APPROVAL-REQUIRED";

    private final ApprovalMapper approvalMapper;
    private final ObjectMapper objectMapper;

    public ApprovalIngestService(
            ApprovalMapper approvalMapper,
            ObjectMapper objectMapper
    ) {
        this.approvalMapper = approvalMapper;
        this.objectMapper = objectMapper;
    }

    public void record(
            String userId,
            String runId,
            String conversationId,
            String rawEventJson
    ) {
        if (rawEventJson == null || rawEventJson.isBlank()) {
            return;
        }
        String payload = rawEventJson.trim();
        if (!payload.startsWith("{")) {
            // Keep-alive comments and "[DONE]" sentinels are not JSON events.
            return;
        }

        try {
            JsonNode event = objectMapper.readTree(payload);
            if (!EVENT_TYPE.equals(event.path("type").asText())) {
                return;
            }

            ApprovalTask task = build(userId, runId, event);
            approvalMapper.upsertFromEvent(task);
        } catch (Exception error) {
            log.warn(
                    "Failed to record approval_required event (run={}, conversation={}): {}",
                    runId,
                    conversationId,
                    error.toString()
            );
        }
    }

    private ApprovalTask build(String userId, String runId, JsonNode event) {
        String requestedAction = text(event, "requested_action");
        String turnId = text(event, "turn_id");
        if (turnId == null) {
            turnId = runId;
        }

        String approvalId = text(event, "approval_id");
        if (approvalId == null) {
            approvalId = "approval_" + sha256Hex(runId + ":" + requestedAction)
                    .substring(0, 24);
        }

        String approvalKey = text(event, "approval_key");
        if (approvalKey == null) {
            approvalKey = sha256Hex(turnId + ":" + requestedAction + ":approval");
        }

        String eventUserId = text(event, "user_id");
        String agentId = text(event, "agent_id");
        String ruleId = text(event, "rule_id");

        LocalDateTime now = LocalDateTime.now();
        ApprovalTask task = new ApprovalTask();
        task.setId(approvalId);
        task.setRunId(runId);
        task.setTurnId(turnId);
        task.setThreadId(turnId);
        task.setCheckpointId(text(event, "checkpoint_id"));
        task.setCheckpointNs(text(event, "checkpoint_ns"));
        task.setInterruptId(text(event, "interrupt_id"));
        task.setApprovalKey(approvalKey);
        task.setUserId(eventUserId == null ? userId : eventUserId);
        task.setAgentId(agentId == null ? DEFAULT_AGENT_ID : agentId);
        task.setIntent(text(event, "intent"));
        task.setRiskLevel(text(event, "risk_level"));
        task.setRuleId(ruleId == null ? DEFAULT_RULE_ID : ruleId);
        // Optional on the wire: the agent may only learn the required roles on a later re-emit,
        // which the upsert merges with COALESCE instead of overwriting with NULL.
        task.setRequiredApproverRoles(text(event, "required_approver_roles"));
        task.setStatus("pending");
        task.setResumeState("queued");
        task.setResumeAttempts(0);
        task.setCreatedAt(now);
        task.setUpdatedAt(now);
        return task;
    }

    private String text(JsonNode event, String field) {
        JsonNode node = event.path(field);
        if (node.isMissingNode() || node.isNull()) {
            return null;
        }
        String value = node.asText();
        return value == null || value.isBlank() ? null : value;
    }

    private static String sha256Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(
                    digest.digest(value.getBytes(StandardCharsets.UTF_8))
            );
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is not available", error);
        }
    }
}
