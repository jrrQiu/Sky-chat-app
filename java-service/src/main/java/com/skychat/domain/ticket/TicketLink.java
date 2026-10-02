package com.skychat.domain.ticket;

import java.time.LocalDateTime;

/**
 * A relation between two tickets, stored in one direction only.
 *
 * <p>Reading the other end derives {@link TicketLinkType#inverse()} and marks it as incoming,
 * so "A blocks B" is visible from both tickets without a second row that could go stale.</p>
 */
public class TicketLink {
    private String id;
    private String ticketId;
    private String linkedTicketId;
    private String linkType;
    private String createdBy;
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

    public String getLinkedTicketId() {
        return linkedTicketId;
    }

    public void setLinkedTicketId(String linkedTicketId) {
        this.linkedTicketId = linkedTicketId;
    }

    public String getLinkType() {
        return linkType;
    }

    public void setLinkType(String linkType) {
        this.linkType = linkType;
    }

    public String getCreatedBy() {
        return createdBy;
    }

    public void setCreatedBy(String createdBy) {
        this.createdBy = createdBy;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
