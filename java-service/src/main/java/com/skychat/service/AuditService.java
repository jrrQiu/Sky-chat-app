package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.mapper.AuditLogMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.Map;
import java.util.UUID;

/**
 * Writes the administrative/security audit trail.
 *
 * <p>Deliberately not used for read-only endpoints: following the Zammad rule, the audit log
 * records administrative and security actions, not day-to-day reads. Recording every list or
 * fetch would drown the trail in noise and turn the log into a second access log.</p>
 *
 * <p>An audit failure must never break the request it describes, so every write is wrapped and
 * downgraded to a WARN. The trade-off is explicit: availability of the business operation wins
 * over completeness of the trail.</p>
 */
@Service
public class AuditService {
    private static final Logger log = LoggerFactory.getLogger(AuditService.class);

    public static final String ACTOR_USER = "user";
    public static final String ACTOR_SYSTEM = "system";
    public static final String OUTCOME_SUCCESS = "success";
    public static final String OUTCOME_FAILURE = "failure";
    public static final String OUTCOME_DENIED = "denied";
    public static final String OUTCOME_LOCKED = "locked";

    /**
     * Request metadata that is not part of the business signature.
     *
     * <p>Mutable setters keep controller call sites readable while still defaulting to the
     * system actor for background work.</p>
     */
    public static final class Context {
        private String actorId;
        private String actorType = ACTOR_SYSTEM;
        private String sourceIp;
        private String userAgent;

        public static Context system() {
            return new Context();
        }

        public static Context user(String actorId, String sourceIp, String userAgent) {
            Context context = new Context();
            context.actorId = actorId;
            context.actorType = ACTOR_USER;
            context.sourceIp = sourceIp;
            context.userAgent = userAgent;
            return context;
        }

        public Context actor(String actorId) {
            this.actorId = actorId;
            return this;
        }

        public String actorId() {
            return actorId;
        }

        public String actorType() {
            return actorType;
        }

        public String sourceIp() {
            return sourceIp;
        }

        public String userAgent() {
            return userAgent;
        }
    }

    private final AuditLogMapper auditLogMapper;
    private final ObjectMapper objectMapper;

    public AuditService(AuditLogMapper auditLogMapper, ObjectMapper objectMapper) {
        this.auditLogMapper = auditLogMapper;
        this.objectMapper = objectMapper;
    }

    public void record(
            String actorId,
            String actorType,
            String action,
            String objectType,
            String objectId,
            String outcome,
            String sourceIp,
            String userAgent,
            Map<String, Object> detail
    ) {
        Context context = new Context();
        context.actorId = actorId;
        context.actorType = actorType == null || actorType.isBlank() ? ACTOR_SYSTEM : actorType;
        context.sourceIp = sourceIp;
        context.userAgent = userAgent;
        record(context, action, objectType, objectId, outcome, detail);
    }

    /**
     * Convenience overload for the common "who did what to which object" call.
     */
    public void record(
            Context context,
            String action,
            String objectType,
            String objectId,
            String outcome,
            Map<String, Object> detail
    ) {
        try {
            String detailJson = objectMapper.writeValueAsString(detail == null ? Map.of() : detail);
            auditLogMapper.insert(
                    UUID.randomUUID().toString(),
                    LocalDateTime.now(),
                    truncate(context == null ? null : context.actorId(), 64),
                    truncate(context == null ? ACTOR_SYSTEM : context.actorType(), 16),
                    truncate(action, 64),
                    truncate(objectType, 32),
                    truncate(objectId, 64),
                    truncate(outcome == null ? OUTCOME_SUCCESS : outcome, 16),
                    truncate(context == null ? null : context.sourceIp(), 64),
                    truncate(context == null ? null : context.userAgent(), 255),
                    detailJson
            );
        } catch (Exception error) {
            // Never let the trail break the request it describes.
            log.warn("Audit write failed (action={}, object={}): {}", action, objectId, error.toString());
        }
    }

    private String truncate(String value, int max) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.length() <= max ? value : value.substring(0, max);
    }
}
