package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.support.InMemoryConversationSummaryMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The boundary between a streamed answer and durable state.
 *
 * <p>These are the two rules that make the new {@code context_report} event safe to
 * add to an existing protocol: unknown event types are ignored (so a client and
 * server can be upgraded independently), and an unreadable frame never takes the
 * turn down with it.
 */
class StreamEventAccumulatorTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private StreamEventAccumulator accumulator;

    @BeforeEach
    void setUp() {
        accumulator = new StreamEventAccumulator(objectMapper);
    }

    @Test
    void collectsTheAnswerTitleAndContextReport() {
        accumulator.accept("{\"type\":\"answer\",\"content\":\"你\"}");
        accumulator.accept("{\"type\":\"answer\",\"content\":\"好\"}");
        accumulator.accept("{\"type\":\"conversation_title\",\"content\":\"VPN 申请\"}");
        accumulator.accept("""
            {"type":"context_report","report":{"used_tokens":1200,"usable_tokens":8192,
             "summary_delta":{"covered_until_message_id":"m18","text":"事实：网络运维"}}}
            """);

        assertThat(accumulator.assistantText()).isEqualTo("你好");
        assertThat(accumulator.title()).isEqualTo("VPN 申请");
        assertThat(accumulator.failed()).isFalse();
        JsonNode report = accumulator.contextReport();
        assertThat(report).isNotNull();
        assertThat(report.path("used_tokens").asInt()).isEqualTo(1200);
        assertThat(report.path("summary_delta").path("covered_until_message_id").asText())
                .isEqualTo("m18");
    }

    @Test
    void theNewestReportWins() {
        // A resumed run re-assembles the prompt, so the last report is the one that
        // describes the answer being persisted.
        accumulator.accept("{\"type\":\"context_report\",\"report\":{\"used_tokens\":1}}");
        accumulator.accept("{\"type\":\"context_report\",\"report\":{\"used_tokens\":2}}");

        assertThat(accumulator.contextReport().path("used_tokens").asInt()).isEqualTo(2);
    }

    @Test
    void unknownEventTypesAreIgnored() {
        accumulator.accept("{\"type\":\"thinking\",\"content\":\"…\"}");
        accumulator.accept("{\"type\":\"tool_call\",\"name\":\"ticket_create\"}");
        accumulator.accept("{\"type\":\"something_from_the_future\",\"x\":1}");

        assertThat(accumulator.assistantText()).isEmpty();
        assertThat(accumulator.contextReport()).isNull();
        assertThat(accumulator.failed()).isFalse();
    }

    @Test
    void anUnreadableFrameDoesNotFailTheTurn() {
        accumulator.accept("not json at all");
        accumulator.accept("");
        accumulator.accept("{\"type\":\"error\",\"message\":\"模型服务调用失败\"}");
        accumulator.accept("{\"type\":\"answer\",\"content\":\"仍然有答案\"}");

        assertThat(accumulator.assistantText()).isEqualTo("仍然有答案");
        // The error frame is still recognised: a failed run must not look successful.
        assertThat(accumulator.failed()).isTrue();
    }

    @Test
    void aFalseyReportIsNotStored() {
        accumulator.accept("{\"type\":\"context_report\",\"report\":null}");
        assertThat(accumulator.contextReport()).isNull();
    }

    @Test
    void theReportedDeltaIsWhatGetsPersisted() {
        InMemoryConversationSummaryMapper summaries = new InMemoryConversationSummaryMapper();
        ConversationSummaryService summaryService = new ConversationSummaryService(
                summaries,
                null,
                objectMapper
        );

        accumulator.accept("""
            {"type":"context_report","report":{"summary_delta":{
              "covered_from_message_id":"m1","covered_until_message_id":"m18",
              "fields":{"facts":["用户所在部门是网络运维"]},"text":"事实：网络运维"}}}
            """);
        summaryService.persistRolling("c1", accumulator.contextReport().path("summary_delta"));

        assertThat(summaries.countForConversation("c1")).isEqualTo(1);
        assertThat(summaries.findLatest("c1").getCoveredFromMessageId()).isEqualTo("m1");
        assertThat(summaries.findLatest("c1").getCoveredUntilMessageId()).isEqualTo("m18");
    }
}
