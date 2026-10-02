package com.skychat.domain.ticket;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One entry of the append-only lifecycle history: who changed what, from what, to what.
 *
 * <p>Separate from {@link TicketComment} on purpose. Mixing "the customer said X" with
 * "the status changed" produces either an unauditable timeline or an unreadable conversation
 * (the Znuny/Zammad split: an immutable communication log plus a lifecycle history).</p>
 */
public class TicketActivity {
    public static final String CREATED = "created";
    public static final String STATUS_CHANGED = "status_changed";
    public static final String ASSIGNED = "assigned";
    public static final String PRIORITY_CHANGED = "priority_changed";
    public static final String FIELDS_UPDATED = "fields_updated";
    public static final String COMMENT_ADDED = "comment_added";
    public static final String LINKED = "linked";
    public static final String UNLINKED = "unlinked";
    public static final String MERGED = "merged";
    public static final String SPLIT = "split";

    private String id;
    private String ticketId;
    private String actorId;
    private String activityType;
    private String fromValue;
    private String toValue;
    private Map<String, Object> detail = new LinkedHashMap<>();
    private LocalDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getTicketId() {
        return ticketId;
    }

    public void setTicketId(String ticketId) {
        this.ticketId = ticketId;
    }

    public String getActorId() {
        return actorId;
    }

    public void setActorId(String actorId) {
        this.actorId = actorId;
    }

    public String getActivityType() {
        return activityType;
    }

    public void setActivityType(String activityType) {
        this.activityType = activityType;
    }

    public String getFromValue() {
        return fromValue;
    }

    public void setFromValue(String fromValue) {
        this.fromValue = fromValue;
    }

    public String getToValue() {
        return toValue;
    }

    public void setToValue(String toValue) {
        this.toValue = toValue;
    }

    public Map<String, Object> getDetail() {
        return detail;
    }

    public void setDetail(Map<String, Object> detail) {
        this.detail = detail == null ? new LinkedHashMap<>() : detail;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
