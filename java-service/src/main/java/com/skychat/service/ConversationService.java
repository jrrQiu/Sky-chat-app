package com.skychat.service;

import com.skychat.domain.Conversation;
import com.skychat.mapper.ConversationMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
public class ConversationService {
    private final ConversationMapper conversationMapper;

    public ConversationService(ConversationMapper conversationMapper) {
        this.conversationMapper = conversationMapper;
    }

    public List<Conversation> list(String userId) {
        return conversationMapper.findByUserId(userId);
    }

    public Conversation findById(String id, String userId) {
        return conversationMapper.findById(id, userId);
    }

    public Conversation create(String userId, String title) {
        LocalDateTime now = LocalDateTime.now();
        Conversation conversation = new Conversation();
        conversation.setId(UUID.randomUUID().toString());
        conversation.setUserId(userId);
        conversation.setTitle(title == null || title.isBlank() ? "新对话" : title);
        conversation.setCreatedAt(now);
        conversation.setUpdatedAt(now);
        conversation.setPinned(false);
        conversationMapper.insert(conversation);
        return conversation;
    }

    public boolean delete(String userId, String id) {
        return conversationMapper.delete(id, userId) > 0;
    }

    public boolean updateTitle(String id, String userId, String title) {
        if (title == null || title.isBlank()) {
            return false;
        }
        return conversationMapper.updateTitle(
                id,
                userId,
                title.trim(),
                LocalDateTime.now()
        ) > 0;
    }

    public boolean touch(String id, String userId) {
        return conversationMapper.touch(id, userId, LocalDateTime.now()) > 0;
    }
}
