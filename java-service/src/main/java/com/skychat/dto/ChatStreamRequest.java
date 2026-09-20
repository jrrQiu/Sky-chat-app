package com.skychat.dto;

import java.util.ArrayList;
import java.util.List;

public class ChatStreamRequest {
    private String content;
    private String conversationId;
    private String userMessageId;
    private String aiMessageId;
    private String runId;
    private String model;
    private boolean enableThinking;
    private boolean enableWebSearch;
    private List<ChatMessageInput> messages = new ArrayList<>();

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public String getUserMessageId() {
        return userMessageId;
    }

    public void setUserMessageId(String userMessageId) {
        this.userMessageId = userMessageId;
    }

    public String getAiMessageId() {
        return aiMessageId;
    }

    public void setAiMessageId(String aiMessageId) {
        this.aiMessageId = aiMessageId;
    }

    public String getRunId() {
        return runId;
    }

    public void setRunId(String runId) {
        this.runId = runId;
    }

    public String getModel() {
        return model;
    }

    public void setModel(String model) {
        this.model = model;
    }

    public boolean isEnableThinking() {
        return enableThinking;
    }

    public void setEnableThinking(boolean enableThinking) {
        this.enableThinking = enableThinking;
    }

    public boolean isEnableWebSearch() {
        return enableWebSearch;
    }

    public void setEnableWebSearch(boolean enableWebSearch) {
        this.enableWebSearch = enableWebSearch;
    }

    public List<ChatMessageInput> getMessages() {
        return messages;
    }

    public void setMessages(List<ChatMessageInput> messages) {
        this.messages = messages == null ? new ArrayList<>() : messages;
    }
}
