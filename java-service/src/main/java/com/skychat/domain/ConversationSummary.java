package com.skychat.domain;

import java.time.LocalDateTime;

public class ConversationSummary {
    private String conversationId;
    private int version;
    private String summary;
    private String startMessageId;
    private String endMessageId;
    private int tokenCount;
    private LocalDateTime createdAt;

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
    }

    public String getSummary() {
        return summary;
    }

    public void setSummary(String summary) {
        this.summary = summary;
    }

    public String getStartMessageId() {
        return startMessageId;
    }

    public void setStartMessageId(String startMessageId) {
        this.startMessageId = startMessageId;
    }

    public String getEndMessageId() {
        return endMessageId;
    }

    public void setEndMessageId(String endMessageId) {
        this.endMessageId = endMessageId;
    }

    public int getTokenCount() {
        return tokenCount;
    }

    public void setTokenCount(int tokenCount) {
        this.tokenCount = tokenCount;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
