package com.skychat.service;

import com.skychat.domain.ConversationSummary;
import com.skychat.domain.Message;
import com.skychat.mapper.ConversationSummaryMapper;
import com.skychat.mapper.MessageMapper;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class ConversationSummaryService {
    private static final int RECENT_LIMIT = 20;
    private static final int MAX_SUMMARY_CHARS = 2400;

    private final ConversationSummaryMapper summaryMapper;
    private final MessageMapper messageMapper;

    public ConversationSummaryService(
            ConversationSummaryMapper summaryMapper,
            MessageMapper messageMapper
    ) {
        this.summaryMapper = summaryMapper;
        this.messageMapper = messageMapper;
    }

    public void roll(String conversationId) {
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
        row.setTokenCount(Math.max(1, summary.length() / 3));
        summaryMapper.insert(row);
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
        if (summary.length() > MAX_SUMMARY_CHARS) {
            return summary.substring(0, MAX_SUMMARY_CHARS);
        }
        return summary;
    }
}
