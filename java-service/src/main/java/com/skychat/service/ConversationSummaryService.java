package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.ConversationSummary;
import com.skychat.domain.Message;
import com.skychat.mapper.ConversationSummaryMapper;
import com.skychat.mapper.MessageMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Persistence for conversation summaries.
 *
 * <p>Two responsibilities, deliberately kept apart:
 *
 * <ul>
 *   <li>{@link #persistRolling} is the real path. The agent service's context engine
 *       decides which messages its summary covers and reports the delta; this class
 *       only writes it. The decision is not duplicated here because Java has no
 *       tokenizer and cannot see the prompt.</li>
 *   <li>{@link #roll} is the legacy per-turn concatenation. It is kept only so an
 *       operator can switch back, and it is <strong>off by default</strong> because
 *       its behaviour was harmful: it overwrote the summary on every turn with the
 *       two newest messages, so the older history was permanently lost while the
 *       "summary" contained nothing the raw window did not already have.</li>
 * </ul>
 */
@Service
public class ConversationSummaryService {
    private static final Logger log = LoggerFactory.getLogger(ConversationSummaryService.class);
    private static final int RECENT_LIMIT = 20;
    private static final int MAX_SUMMARY_CHARS = 2400;

    private final ConversationSummaryMapper summaryMapper;
    private final MessageMapper messageMapper;
    private final ObjectMapper objectMapper;

    /**
     * The rolling summary from the context engine is authoritative. This flag only
     * controls the legacy fallback, and defaults to off so the old overwrite cannot
     * silently return.
     */
    @Value("${skychat.summary.legacy-roll-enabled:false}")
    private boolean legacyRollEnabled;

    public ConversationSummaryService(
            ConversationSummaryMapper summaryMapper,
            MessageMapper messageMapper,
            ObjectMapper objectMapper
    ) {
        this.summaryMapper = summaryMapper;
        this.messageMapper = messageMapper;
        this.objectMapper = objectMapper;
    }

    /**
     * Persist a summary revision reported by the context engine.
     *
     * @param conversationId the conversation the summary belongs to
     * @param delta the `summary_delta` object from a `context_report` SSE event
     * @return true when a new version was written
     */
    public boolean persistRolling(String conversationId, JsonNode delta) {
        if (conversationId == null || conversationId.isBlank() || delta == null || delta.isNull()) {
            return false;
        }
        String until = text(delta, "covered_until_message_id");
        String from = text(delta, "covered_from_message_id");
        String summary = text(delta, "text");
        if (until.isBlank() || summary.isBlank()) {
            // Nothing to cover, or nothing to say. Writing an empty version would
            // advance the version counter without adding information.
            return false;
        }

        ConversationSummary latest = summaryMapper.findLatest(conversationId);
        if (latest != null && until.equals(latest.getCoveredUntilMessageId())) {
            // This range is already covered: the engine re-reported the same
            // eviction (a retry, or a turn where nothing new fell out of the
            // window). Writing again would create duplicate versions.
            return false;
        }

        ConversationSummary row = new ConversationSummary();
        row.setConversationId(conversationId);
        row.setVersion(latest == null ? 1 : latest.getVersion() + 1);
        row.setSummary(truncate(summary));
        row.setStartMessageId(from.isBlank() ? null : from);
        row.setEndMessageId(until);
        row.setCoveredFromMessageId(from.isBlank() ? null : from);
        row.setCoveredUntilMessageId(until);
        row.setFieldValues(fieldsJson(delta.get("fields")));
        row.setTokenCount(Math.max(1, summary.length() / 3));

        int written;
        try {
            written = summaryMapper.insertIfVersionFree(row);
        } catch (RuntimeException error) {
            // Summary persistence must never fail the turn: the answer is already
            // streamed and the loss is recoverable on the next eviction.
            log.warn("Could not persist rolling summary for {}", conversationId, error);
            return false;
        }
        if (written == 0) {
            log.debug("Rolling summary version {} was taken concurrently for {}",
                    row.getVersion(), conversationId);
        }
        return written > 0;
    }

    /**
     * Legacy per-turn summary. Disabled by default; see the class comment.
     */
    public void roll(String conversationId) {
        if (!legacyRollEnabled) {
            return;
        }
        List<Message> messages = messageMapper.findRecentByConversationId(
                conversationId,
                RECENT_LIMIT
        );
        if (messages.isEmpty()) {
            return;
        }

        Message first = messages.get(0);
        Message last = messages.get(messages.size() - 1);
        String summary = buildSummary(messages);
        if (summary.isBlank()) {
            return;
        }

        ConversationSummary latest = summaryMapper.findLatest(conversationId);
        int nextVersion = latest == null ? 1 : latest.getVersion() + 1;

        ConversationSummary row = new ConversationSummary();
        row.setConversationId(conversationId);
        row.setVersion(nextVersion);
        row.setSummary(summary);
        row.setStartMessageId(first.getId());
        row.setEndMessageId(last.getId());
        row.setCoveredFromMessageId(first.getId());
        row.setCoveredUntilMessageId(last.getId());
        row.setTokenCount(Math.max(1, summary.length() / 3));
        summaryMapper.insertIfVersionFree(row);
    }

    private String buildSummary(List<Message> messages) {
        String lastUser = "";
        String lastAssistant = "";
        for (int index = messages.size() - 1; index >= 0; index -= 1) {
            Message message = messages.get(index);
            if (lastUser.isBlank() && "user".equals(message.getRole())) {
                lastUser = message.getContent();
            }
            if (lastAssistant.isBlank() && "assistant".equals(message.getRole())) {
                lastAssistant = message.getContent();
            }
            if (!lastUser.isBlank() && !lastAssistant.isBlank()) {
                break;
            }
        }

        String summary = "用户：" + lastUser + "；助手：" + lastAssistant;
        return truncate(summary);
    }

    private String fieldsJson(JsonNode fields) {
        if (fields == null || fields.isNull() || !fields.isObject()) {
            return "{}";
        }
        try {
            return objectMapper.writeValueAsString(fields);
        } catch (Exception error) {
            log.warn("Could not serialise summary fields; storing an empty object", error);
            return "{}";
        }
    }

    private String text(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? "" : value.asText("");
    }

    private String truncate(String value) {
        if (value == null) {
            return "";
        }
        return value.length() > MAX_SUMMARY_CHARS
                ? value.substring(0, MAX_SUMMARY_CHARS)
                : value;
    }
}
