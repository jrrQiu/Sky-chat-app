package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.support.InMemoryConversationSummaryMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The write side of a context report: {@code summary_delta} becomes a summary row.
 *
 * <p>End-to-end the event arrives inside a stream and is handled by
 * {@link StreamEventAccumulator}; this test takes the report it produces and drives
 * the persistence, which is the half that depends on the database.</p>
 */
class ChatStreamServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private InMemoryConversationSummaryMapper summaries;
    private ConversationSummaryService summaryService;

    @BeforeEach
    void setUp() {
        summaries = new InMemoryConversationSummaryMapper();
        summaryService = new ConversationSummaryService(summaries, null, objectMapper);
    }

    @Test
    void aStreamedReportPersistsTheRangeItCovered() {
        StreamEventAccumulator accumulator = new StreamEventAccumulator(objectMapper);
        accumulator.accept("{\"type\":\"answer\",\"content\":\"你好\"}");
        accumulator.accept("""
            {"type":"context_report","report":{"used_tokens":1200,"usable_tokens":8192,
             "summary_delta":{"covered_from_message_id":"m1",
                              "covered_until_message_id":"m18",
                              "fields":{"facts":["用户所在部门是网络运维"]},
                              "text":"事实：网络运维","quality":"model"}}}
            """);

        assertThat(accumulator.assistantText()).isEqualTo("你好");
        boolean written = summaryService.persistRolling(
                "c1",
                accumulator.contextReport().path("summary_delta")
        );

        assertThat(written).isTrue();
        assertThat(summaries.findLatest("c1").getCoveredUntilMessageId()).isEqualTo("m18");
        assertThat(summaries.findLatest("c1").getFieldValues()).contains("网络运维");
    }

    @Test
    void aReportWithoutADeltaWritesNothing() {
        StreamEventAccumulator accumulator = new StreamEventAccumulator(objectMapper);
        accumulator.accept(
                "{\"type\":\"context_report\",\"report\":{\"used_tokens\":10,\"usable_tokens\":100}}"
        );

        // The key is absent, so `path` yields a missing node; the service must treat
        // that as "no summary owed" rather than writing an empty version.
        boolean written = summaryService.persistRolling(
                "c1",
                accumulator.contextReport().path("summary_delta")
        );

        assertThat(written).isFalse();
        assertThat(summaries.insertAttempts.get()).isZero();
    }

    @Test
    void aReplayedReportDoesNotCreateASecondVersion() {
        StreamEventAccumulator accumulator = new StreamEventAccumulator(objectMapper);
        accumulator.accept("""
            {"type":"context_report","report":{"summary_delta":{
              "covered_from_message_id":"m1","covered_until_message_id":"m18",
              "text":"事实：网络运维"}}}
            """);

        assertThat(summaryService.persistRolling(
                "c1", accumulator.contextReport().path("summary_delta"))).isTrue();
        assertThat(summaryService.persistRolling(
                "c1", accumulator.contextReport().path("summary_delta"))).isFalse();
        assertThat(summaries.countForConversation("c1")).isEqualTo(1);
    }
}
