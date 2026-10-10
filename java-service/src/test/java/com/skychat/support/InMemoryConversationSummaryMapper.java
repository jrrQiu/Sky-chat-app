package com.skychat.support;

import com.skychat.domain.ConversationSummary;
import com.skychat.mapper.ConversationSummaryMapper;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * In-memory {@link ConversationSummaryMapper} that honours the two behaviours the
 * service depends on: newest-version-first reads, and "the version number is a
 * unique key" (so a lost race returns 0 rather than clobbering the winner).
 */
public class InMemoryConversationSummaryMapper implements ConversationSummaryMapper {

    private final List<ConversationSummary> rows = new ArrayList<>();
    /** Counts insert attempts so a test can prove a duplicate write was skipped. */
    public final AtomicInteger insertAttempts = new AtomicInteger();
    public final AtomicInteger conflicts = new AtomicInteger();

    @Override
    public ConversationSummary findLatest(String conversationId) {
        return rows.stream()
                .filter(row -> row.getConversationId().equals(conversationId))
                .max(Comparator.comparingInt(ConversationSummary::getVersion))
                .orElse(null);
    }

    @Override
    public int insert(ConversationSummary summary) {
        return insertIfVersionFree(summary);
    }

    @Override
    public int insertIfVersionFree(ConversationSummary summary) {
        insertAttempts.incrementAndGet();
        boolean taken = rows.stream().anyMatch(row ->
                row.getConversationId().equals(summary.getConversationId())
                        && row.getVersion() == summary.getVersion());
        if (taken) {
            conflicts.incrementAndGet();
            return 0;
        }
        rows.add(summary);
        return 1;
    }

    @Override
    public int countForConversation(String conversationId) {
        return (int) rows.stream()
                .filter(row -> row.getConversationId().equals(conversationId))
                .count();
    }

    public List<ConversationSummary> all() {
        return List.copyOf(rows);
    }
}
