package com.skychat.domain.ticket;

import java.time.LocalDateTime;

/**
 * One entry of the immutable communication log.
 *
 * <p>There is no update or delete path for a comment, by design: the record of what was said
 * is evidence, and an editable communication log is not evidence. A correction is a new
 * comment.</p>
 */
public class TicketComment {
    public static final String VISIBILITY_PUBLIC = "public";
    /** Agent-only. Must never be returned to a requester. */
    public static final String VISIBILITY_INTERNAL = "internal";

    private String id;
    private String ticketId;
    private String authorId;
    /** Snapshot of the author's roles at the time of writing, for the audit trail. */
    private String authorRoles = "";
    private String visibility = VISIBILITY_PUBLIC;
    private String body;
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

    public String getAuthorId() {
        return authorId;
    }

    public void setAuthorId(String authorId) {
        this.authorId = authorId;
    }

    public String getAuthorRoles() {
        return authorRoles;
    }

    public void setAuthorRoles(String authorRoles) {
        this.authorRoles = authorRoles;
    }

    public String getVisibility() {
        return visibility;
    }

    public void setVisibility(String visibility) {
        this.visibility = visibility;
    }

    public String getBody() {
        return body;
    }

    public void setBody(String body) {
        this.body = body;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public boolean isInternal() {
        return VISIBILITY_INTERNAL.equalsIgnoreCase(visibility);
    }
}
