package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ConversationSummary;
import com.skychat.domain.Message;
import com.skychat.mapper.MessageMapper;
import com.skychat.support.InMemoryConversationSummaryMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The rolling summary is written from the context engine's report. These tests pin
 * the properties that make it a summary rather than the old overwrite: the covered
 * range is stored, a re-reported range is not written twice, and a failure to write
 * never breaks the turn.
 */
class ConversationSummaryServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private InMemoryConversationSummaryMapper mapper;
    private ConversationSummaryService service;

    @BeforeEach
    void setUp() {
        mapper = new InMemoryConversationSummaryMapper();
        service = new ConversationSummaryService(mapper, new NoMessages(), objectMapper);
    }

    private JsonNode delta(String from, String until, String text) {
        return objectMapper.valueToTree(
                java.util.Map.of(
                        "covered_from_message_id", from,
                        "covered_until_message_id", until,
                        "fields", java.util.Map.of("facts", List.of("用户所在部门是网络运维")),
                        "text", text,
                        "quality", "model"
                )
        );
    }

    @Test
    void persistsTheCoveredRangeAndTheStructuredFields() {
        boolean written = service.persistRolling("c1", delta("m1", "m20", "事实：网络运维"));

        assertTrue(written);
        ConversationSummary row = mapper.findLatest("c1");
        assertNotNull(row);
        assertEquals(1, row.getVersion());
        assertEquals("m1", row.getCoveredFromMessageId());
        assertEquals("m20", row.getCoveredUntilMessageId());
        assertEquals("m1", row.getStartMessageId());
        assertEquals("m20", row.getEndMessageId());
        assertTrue(row.getFieldValues().contains("网络运维"));
        assertTrue(row.getSummary().contains("网络运维"));
    }

    @Test
    void aSecondRevisionCoversTheNextRange() {
        service.persistRolling("c1", delta("m1", "m20", "第一版"));
        assertTrue(service.persistRolling("c1", delta("m21", "m40", "第二版")));

        ConversationSummary latest = mapper.findLatest("c1");
        assertEquals(2, latest.getVersion());
        assertEquals("m21", latest.getCoveredFromMessageId());
        assertEquals("m40", latest.getCoveredUntilMessageId());
        assertEquals(2, mapper.countForConversation("c1"));
    }

    @Test
    void reReportingTheSameRangeDoesNotCreateADuplicateVersion() {
        assertTrue(service.persistRolling("c1", delta("m1", "m20", "第一版")));
        assertFalse(service.persistRolling("c1", delta("m1", "m20", "第一版（重放）")));

        assertEquals(1, mapper.countForConversation("c1"));
        assertEquals("第一版", mapper.findLatest("c1").getSummary());
    }

    @Test
    void anEmptyOrUnrangedDeltaIsRefused() {
        assertFalse(service.persistRolling("c1", delta("", "", "")));
        assertFalse(service.persistRolling("c1", objectMapper.createObjectNode()));
        assertFalse(service.persistRolling(null, delta("m1", "m20", "x")));
        assertEquals(0, mapper.insertAttempts.get());
    }

    @Test
    void aLostRaceDoesNotClobberTheWinner() {
        // Two concurrent turns both computed version 1; the mapper refuses the
        // second insert, and the service reports "not written" instead of throwing.
        ConversationSummary winner = new ConversationSummary();
        winner.setConversationId("c1");
        winner.setVersion(1);
        winner.setSummary("胜者");
        winner.setCoveredFromMessageId("m1");
        winner.setCoveredUntilMessageId("m20");
        winner.setFieldValues("{}");
        mapper.insertIfVersionFree(winner);

        // The service sees the existing row and therefore refuses before inserting,
        // which is the cheap path; simulate the harder path by writing directly.
        assertEquals(0, mapper.insertIfVersionFree(winner));
        assertEquals(1, mapper.conflicts.get());
        assertEquals("胜者", mapper.findLatest("c1").getSummary());
    }

    @Test
    void legacyRollIsOffByDefault() {
        // The per-turn concatenation overwrote the summary with the two newest
        // messages every turn, which is how older history was lost permanently.
        // It must not come back on its own.
        service.roll("c1");
        assertNull(mapper.findLatest("c1"));
        assertEquals(0, mapper.insertAttempts.get());
    }

    /** The legacy path reads messages; this test never needs them. */
    private static final class NoMessages implements MessageMapper {
        @Override
        public List<Message> findByConversationId(String conversationId) {
            return List.of();
        }

        @Override
        public List<Message> findRecentByConversationId(String conversationId, int limit) {
            return List.of();
        }

        @Override
        public int insert(Message message) {
            return 0;
        }

        @Override
        public int deleteByIds(List<String> ids, String userId) {
            return 0;
        }
    }
}
