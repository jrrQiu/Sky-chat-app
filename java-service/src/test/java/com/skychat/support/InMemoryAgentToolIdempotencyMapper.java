package com.skychat.support;

import com.skychat.mapper.AgentToolIdempotencyMapper;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * In-memory {@link AgentToolIdempotencyMapper} that honours the two behaviours the service
 * depends on: newest-first reads by {@code (tool, key)}, and "the key is unique" — so a second
 * insert returns 0 instead of overwriting the first result.
 */
public class InMemoryAgentToolIdempotencyMapper implements AgentToolIdempotencyMapper {

    private final Map<String, String> responses = new LinkedHashMap<>();
    private final Map<String, LocalDateTime> created = new LinkedHashMap<>();
    /** Insert attempts, so a test can prove the second delivery was deduplicated. */
    public final AtomicInteger insertAttempts = new AtomicInteger();
    public final AtomicInteger conflicts = new AtomicInteger();
    public final AtomicInteger deletes = new AtomicInteger();

    private String key(String toolName, String idemKey) {
        return toolName + "\u0000" + idemKey;
    }

    @Override
    public String findResponse(String toolName, String idemKey) {
        return responses.get(key(toolName, idemKey));
    }

    @Override
    public int insertIfAbsent(String toolName, String idemKey, String response, String actorId) {
        insertAttempts.incrementAndGet();
        String composite = key(toolName, idemKey);
        if (responses.containsKey(composite)) {
            conflicts.incrementAndGet();
            return 0;
        }
        responses.put(composite, response);
        created.put(composite, LocalDateTime.now());
        return 1;
    }

    @Override
    public int deleteOlderThan(LocalDateTime before) {
        int removed = 0;
        var iterator = created.entrySet().iterator();
        while (iterator.hasNext()) {
            var entry = iterator.next();
            if (entry.getValue().isBefore(before)) {
                responses.remove(entry.getKey());
                iterator.remove();
                removed += 1;
            }
        }
        deletes.addAndGet(removed);
        return removed;
    }

    @Override
    public int count() {
        return responses.size();
    }

    /** Age a stored key so a retention test can sweep it without waiting. */
    public void backdate(String toolName, String idemKey, LocalDateTime when) {
        created.put(key(toolName, idemKey), when);
    }
}
