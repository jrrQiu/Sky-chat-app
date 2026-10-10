package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.mapper.AgentToolIdempotencyMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Makes a retried agent tool delivery a no-op instead of a second write.
 *
 * <p>The agent service already guarantees at-most-once execution inside its own process: the
 * effect ledger keys a write by turn plus action, and a replay short-circuits. That guarantee
 * stops at the network boundary. A request that is delivered twice -- because a response was
 * lost, or because an operator replayed it -- arrives here indistinguishable from a first
 * attempt, so without this class a duplicate delivery creates a duplicate ticket.</p>
 *
 * <p>The shape is deliberately simple and honest about what it can promise:</p>
 *
 * <ul>
 *   <li><b>Recorded after the fact.</b> The result of a successful call is stored, so a
 *       duplicate returns the same payload (same ticket number) instead of a second ticket.
 *       This is not a reservation: two requests that arrive simultaneously both execute, and
 *       the second one's insert loses. That gap is acceptable because the agent only retries
 *       when the connection was never established, and a lost *response* is surfaced to the
 *       model as a failure rather than retried.</li>
 *   <li><b>Only successful writes are recorded.</b> Caching a failure would turn a transient
 *       error into a permanent one, and there is nothing to deduplicate about a call that did
 *       not change anything.</li>
 *   <li><b>Reads are never deduplicated.</b> A repeated list is supposed to return current
 *       data; serving a cached page would be a different, and worse, bug.</li>
 * </ul>
 */
@Service
public class ToolIdempotencyService {
    private static final Logger log = LoggerFactory.getLogger(ToolIdempotencyService.class);

    /** Tools whose repeated delivery must not repeat the effect. */
    private static final java.util.Set<String> DEDUPLICATED_TOOLS =
            java.util.Set.of("ticket_create", "ticket_comment", "ticket_merge");

    private final AgentToolIdempotencyMapper mapper;
    private final ObjectMapper objectMapper;

    /** Retention window; kept generous because a slow retry is still a retry. */
    @Value("${skychat.tools.idempotency-retention-hours:24}")
    private int retentionHours;

    public ToolIdempotencyService(
            AgentToolIdempotencyMapper mapper,
            ObjectMapper objectMapper
    ) {
        this.mapper = mapper;
        this.objectMapper = objectMapper;
    }

    /** Whether this tool's repeated delivery is deduplicated at all. */
    public boolean deduplicates(String toolName) {
        return DEDUPLICATED_TOOLS.contains(toolName);
    }

    /**
     * The original result for this key, or {@code null} when it has not been seen.
     *
     * <p>Returns a mutable copy marked {@code deduplicated=true}: the marker is what tells the
     * agent (and the audit trail) that the effect happened earlier and this reply is a replay.
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> replay(String toolName, String idemKey) {
        if (!deduplicates(toolName) || blank(idemKey)) {
            return null;
        }
        String stored;
        try {
            stored = mapper.findResponse(toolName, idemKey);
        } catch (RuntimeException error) {
            // A lookup failure must not turn a legitimate write into a refusal: the fallback
            // is the pre-idempotency behaviour (execute), not "refuse everything".
            log.warn("Idempotency lookup failed for {} ({}); executing normally", toolName, idemKey, error);
            return null;
        }
        if (stored == null || stored.isBlank()) {
            return null;
        }
        try {
            Map<String, Object> parsed = objectMapper.readValue(stored, Map.class);
            Map<String, Object> replay = new LinkedHashMap<>(parsed);
            replay.put("deduplicated", true);
            return replay;
        } catch (Exception error) {
            log.warn("Stored idempotency response for {} is unreadable; executing normally", toolName, error);
            return null;
        }
    }

    /**
     * Record a successful result for this key.
     *
     * <p>Never throws into the request path: the write has already happened, and failing here
     * would report a failure for something that succeeded. Losing the record only means the
     * next duplicate is not deduplicated, which is the behaviour we had before this feature.
     */
    public void record(String toolName, String idemKey, Map<String, Object> response, String actorId) {
        if (!deduplicates(toolName) || blank(idemKey) || response == null) {
            return;
        }
        if (!Boolean.TRUE.equals(response.get("success"))) {
            return;
        }
        try {
            String json = objectMapper.writeValueAsString(response);
            int inserted = mapper.insertIfAbsent(toolName, idemKey, json, actorId);
            if (inserted == 0) {
                log.debug("Idempotency key {} for {} was already recorded", idemKey, toolName);
            }
        } catch (Exception error) {
            log.warn("Could not record idempotency key {} for {}", idemKey, toolName, error);
        }
    }

    /**
     * Sweep keys that can no longer plausibly be replayed.
     *
     * <p>A duplicate delivery arrives within seconds or minutes, never a day later, so the
     * table is bounded rather than growing with every tool call forever.
     */
    @Scheduled(fixedDelayString = "${skychat.tools.idempotency-sweep-ms:3600000}")
    public void sweepExpired() {
        try {
            int removed = mapper.deleteOlderThan(LocalDateTime.now().minusHours(Math.max(1, retentionHours)));
            if (removed > 0) {
                log.info("Swept {} expired tool idempotency key(s)", removed);
            }
        } catch (RuntimeException error) {
            log.warn("Could not sweep tool idempotency keys", error);
        }
    }

    /** Reporting view for the health endpoint: is deduplication actually in effect. */
    public Map<String, Object> status() {
        Map<String, Object> status = new LinkedHashMap<>();
        status.put("deduplicatedTools", DEDUPLICATED_TOOLS.stream().sorted().toList());
        status.put("retentionHours", retentionHours);
        try {
            status.put("recordedKeys", mapper.count());
            status.put("active", true);
        } catch (RuntimeException error) {
            // Saying "active" while the table is unreachable would be the exact kind of
            // reassurance this project keeps refusing to give.
            status.put("active", false);
            status.put("reason", error.getClass().getSimpleName());
        }
        return status;
    }

    private boolean blank(String value) {
        return value == null || value.isBlank();
    }
}
