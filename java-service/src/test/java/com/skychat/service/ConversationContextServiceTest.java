package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ApprovalTask;
import com.skychat.domain.ConversationSummary;
import com.skychat.domain.Memory;
import com.skychat.domain.Message;
import com.skychat.domain.UserAccount;
import com.skychat.domain.UserProfile;
import com.skychat.mapper.ApprovalMapper;
import com.skychat.mapper.MemoryMapper;
import com.skychat.mapper.MessageMapper;
import com.skychat.mapper.UserMapper;
import com.skychat.mapper.UserProfileMapper;
import com.skychat.support.InMemoryConversationSummaryMapper;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The envelope Java hands to the agent service.
 *
 * <p>Two defects this pins down: the envelope used to declare `task_slots` and
 * `tool_results` as the literal "{}" / "[]" placeholders, which consumed budget to
 * say nothing; and the summary used to be an unstructured string, which left the
 * engine unable to tell which messages the summary already covered.
 */
class ConversationContextServiceTest {

    private static final String USER = "u1";
    private static final String CONVERSATION = "c1";

    private ConversationContextService service(
            InMemoryConversationSummaryMapper summaries,
            List<Message> messages
    ) {
        return new ConversationContextService(
                new StubUserMapper(),
                new StubUserProfileMapper(),
                summaries,
                new StubMessageMapper(messages),
                new StubApprovalMapper(),
                new StubMemoryMapper(),
                new ObjectMapper()
        );
    }

    @Test
    @SuppressWarnings("unchecked")
    void envelopeDeclaresNoPlaceholderBlocks() {
        Map<String, Object> envelope = service(
                new InMemoryConversationSummaryMapper(), List.of()
        ).build(USER, CONVERSATION);

        assertEquals(2, envelope.get("schema_version"));
        List<Map<String, Object>> blocks =
                (List<Map<String, Object>>) envelope.get("blocks");
        List<String> kinds = blocks.stream().map(block -> (String) block.get("kind")).toList();

        assertFalse(kinds.contains("task_slots"), "a placeholder that says {} is not material");
        assertFalse(kinds.contains("tool_results"), "the graph supplies tool results, not Java");
        // Hard blocks (policy, tenant) and profile/approval must still be present.
        assertTrue(kinds.contains("system_policy"));
        assertTrue(kinds.contains("tenant_profile"));
        assertTrue(kinds.contains("user_profile"));
        assertTrue(kinds.contains("approval_state"));

        Map<String, Object> limits = (Map<String, Object>) envelope.get("limits");
        assertTrue((Integer) limits.get("recent_messages") > 20,
                "the candidate window must be wider than the old永久-loss threshold of 20");
    }

    @Test
    @SuppressWarnings("unchecked")
    void recentMessagesCarryStableIds() {
        List<Message> messages = new ArrayList<>();
        for (int index = 1; index <= 3; index += 1) {
            Message message = new Message();
            message.setId("m" + index);
            message.setRole(index % 2 == 0 ? "assistant" : "user");
            message.setContent("内容 " + index);
            message.setCreatedAt(LocalDateTime.now());
            messages.add(message);
        }

        Map<String, Object> envelope = service(
                new InMemoryConversationSummaryMapper(), messages
        ).build(USER, CONVERSATION);

        List<Map<String, Object>> recent =
                (List<Map<String, Object>>) envelope.get("recent_messages");
        assertEquals(3, recent.size());
        // The id is what makes an eviction reportable: the engine reports which ids
        // left the prompt so the summary can cover exactly that range.
        assertEquals("m1", recent.get(0).get("id"));
        assertEquals("m3", recent.get(2).get("id"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void structuredSummaryCarriesTheCoveredRange() {
        InMemoryConversationSummaryMapper summaries = new InMemoryConversationSummaryMapper();
        ConversationSummary row = new ConversationSummary();
        row.setConversationId(CONVERSATION);
        row.setVersion(4);
        row.setSummary("事实：用户所在部门是网络运维");
        row.setCoveredFromMessageId("m1");
        row.setCoveredUntilMessageId("m40");
        row.setFieldValues("{\"facts\":[\"用户所在部门是网络运维\"]}");
        summaries.insertIfVersionFree(row);

        Map<String, Object> envelope = service(summaries, List.of()).build(USER, CONVERSATION);

        Map<String, Object> summary = (Map<String, Object>) envelope.get("summary");
        assertEquals(4, summary.get("version"));
        assertEquals("m40", summary.get("covered_until_message_id"));
        Map<String, Object> fields = (Map<String, Object>) summary.get("fields");
        assertEquals(List.of("用户所在部门是网络运维"), fields.get("facts"));

        Map<String, Object> block = ((List<Map<String, Object>>) envelope.get("blocks")).stream()
                .filter(item -> "conversation_summary".equals(item.get("kind")))
                .findFirst()
                .orElseThrow();
        Map<String, Object> metadata = (Map<String, Object>) block.get("metadata");
        assertEquals("m40", metadata.get("covered_until_message_id"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void aMalformedFieldBlobDoesNotBreakTheEnvelope() {
        InMemoryConversationSummaryMapper summaries = new InMemoryConversationSummaryMapper();
        ConversationSummary row = new ConversationSummary();
        row.setConversationId(CONVERSATION);
        row.setVersion(1);
        row.setSummary("渲染文本仍然可用");
        row.setCoveredUntilMessageId("m40");
        // Not valid JSON: the rendered text is the part that must survive, so the
        // structured fields degrade to empty rather than failing the turn.
        row.setFieldValues("{not json");
        summaries.insertIfVersionFree(row);

        Map<String, Object> envelope = service(summaries, List.of()).build(USER, CONVERSATION);

        Map<String, Object> summary = (Map<String, Object>) envelope.get("summary");
        assertEquals("m40", summary.get("covered_until_message_id"));
        assertEquals("渲染文本仍然可用", summary.get("text"));
        assertTrue(((Map<String, Object>) summary.get("fields")).isEmpty());
    }

    @Test
    void noSummaryMeansNoStructuredSummaryKey() {
        Map<String, Object> envelope = service(
                new InMemoryConversationSummaryMapper(), List.of()
        ).build(USER, CONVERSATION);

        assertFalse(envelope.containsKey("summary"),
                "an absent summary must be absent, not an empty object to be rendered");
    }

    // --- stubs: these tests are about the envelope's shape, not about SQL ------

    private static final class StubUserMapper implements UserMapper {
        @Override
        public UserAccount findByEmail(String email) {
            return null;
        }

        @Override
        public UserAccount findById(String id) {
            UserAccount account = new UserAccount();
            account.setId(id);
            account.setName("测试员工");
            account.setEmail("test@example.com");
            return account;
        }

        @Override
        public int insert(UserAccount user) {
            return 0;
        }

        @Override
        public List<UserAccount> search(String query, String status, int limit, int offset) {
            return List.of();
        }

        @Override
        public int countSearch(String query, String status) {
            return 0;
        }

        @Override
        public int updateRoles(String id, String roles) {
            return 0;
        }

        @Override
        public int updateStatus(String id, String status, LocalDateTime disabledAt) {
            return 0;
        }

        @Override
        public int deleteById(String id) {
            return 0;
        }

        @Override
        public int countAdministrators() {
            return 1;
        }

        @Override
        public int countFullAdministrators() {
            return 1;
        }
    }

    private static final class StubUserProfileMapper implements UserProfileMapper {
        @Override
        public UserProfile findById(String userId) {
            return null;
        }

        @Override
        public int upsert(String userId, String preferences, String facts) {
            return 0;
        }
    }

    private static final class StubMessageMapper implements MessageMapper {
        private final List<Message> messages;

        StubMessageMapper(List<Message> messages) {
            this.messages = messages;
        }

        @Override
        public List<Message> findByConversationId(String conversationId) {
            return messages;
        }

        @Override
        public List<Message> findRecentByConversationId(String conversationId, int limit) {
            return messages.size() <= limit ? messages : messages.subList(0, limit);
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

    private static final class StubApprovalMapper implements ApprovalMapper {
        @Override
        public List<ApprovalTask> findByUserId(String userId) {
            return List.of();
        }

        @Override
        public ApprovalTask findById(String id, String userId) {
            return null;
        }

        @Override
        public ApprovalTask findByIdInternal(String id) {
            return null;
        }

        @Override
        public ApprovalTask findLatestPending(String userId) {
            return null;
        }

        @Override
        public List<ApprovalTask> findResumeCandidates(int limit) {
            return List.of();
        }

        @Override
        public List<ApprovalTask> findPendingForApprover(
                String userId, String roles, boolean isAdmin, int limit) {
            return List.of();
        }

        @Override
        public int insert(ApprovalTask task) {
            return 0;
        }

        @Override
        public int upsertFromEvent(ApprovalTask task) {
            return 0;
        }

        @Override
        public int updateStatus(
                String id,
                String status,
                String resumeRequestId,
                String resumedExecutionId,
                LocalDateTime resumedAt,
                String decidedBy,
                LocalDateTime decidedAt,
                String decisionComment,
                LocalDateTime updatedAt
        ) {
            return 0;
        }

        @Override
        public int markResumeResult(
                String id,
                String resumeState,
                String resumeError,
                LocalDateTime consumedAt,
                LocalDateTime updatedAt
        ) {
            return 0;
        }
    }

    private static final class StubMemoryMapper implements MemoryMapper {
        @Override
        public List<Memory> findEligible(String userId, int limit) {
            return List.of();
        }
    }
}
