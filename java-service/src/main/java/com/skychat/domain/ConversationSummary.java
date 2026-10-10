package com.skychat.domain;

import java.time.LocalDateTime;

/**
 * One version of a conversation's rolling summary.
 *
 * <p>The summary is produced by the agent service's context engine, which decides
 * <em>which</em> messages it covers. Two properties follow from that and are stored
 * explicitly:
 *
 * <ul>
 *   <li>{@code coveredFrom}/{@code coveredUntilMessageId} delimit the evicted range,
 *       so successive versions cover the conversation with no gap and no overlap. The
 *       old implementation stored the two newest messages and called that a summary,
 *       which meant everything older was lost permanently.</li>
 *   <li>{@code fieldValues} is the structured form
 *       ({@code decisions}/{@code open_items}/{@code facts}/{@code preferences}/
 *       {@code open_approvals}) as JSON. {@code summary} is the rendered text for
 *       display; the structured form is what the next turn's prompt is rebuilt from,
 *       so a rendering change does not require re-summarising.</li>
 * </ul>
 */
public class ConversationSummary {
    private String conversationId;
    private int version;
    private String summary;
    private String startMessageId;
    private String endMessageId;
    private String coveredFromMessageId;
    private String coveredUntilMessageId;
    private String fieldValues;
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

    public String getCoveredFromMessageId() {
        return coveredFromMessageId;
    }

    public void setCoveredFromMessageId(String coveredFromMessageId) {
        this.coveredFromMessageId = coveredFromMessageId;
    }

    public String getCoveredUntilMessageId() {
        return coveredUntilMessageId;
    }

    public void setCoveredUntilMessageId(String coveredUntilMessageId) {
        this.coveredUntilMessageId = coveredUntilMessageId;
    }

    public String getFieldValues() {
        return fieldValues;
    }

    public void setFieldValues(String fieldValues) {
        this.fieldValues = fieldValues;
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
