package com.skychat.service;

import com.skychat.domain.Message;
import com.skychat.mapper.ConversationMapper;
import com.skychat.mapper.MessageMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
public class MessageService {
    private final MessageMapper messageMapper;
    private final ConversationMapper conversationMapper;

    public MessageService(MessageMapper messageMapper, ConversationMapper conversationMapper) {
        this.messageMapper = messageMapper;
        this.conversationMapper = conversationMapper;
    }

    public List<Message> list(String userId, String conversationId) {
        if (conversationMapper.findById(conversationId, userId) == null) {
            return List.of();
        }
        return messageMapper.findByConversationId(conversationId);
    }

    public Message create(String userId, String conversationId, String role, String content) {
        if (conversationMapper.findById(conversationId, userId) == null) {
            return null;
        }

        Message message = new Message();
        message.setId(UUID.randomUUID().toString());
        message.setConversationId(conversationId);
        message.setRole(role);
        message.setContent(content);
        message.setCreatedAt(LocalDateTime.now());
        messageMapper.insert(message);
        return message;
    }

    public int deleteByIds(String userId, List<String> ids) {
        if (ids == null || ids.isEmpty()) {
            return 0;
        }
        return messageMapper.deleteByIds(ids, userId);
    }
}
