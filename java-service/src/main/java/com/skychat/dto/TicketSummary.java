package com.skychat.dto;

import com.skychat.domain.ticket.Ticket;

import java.time.LocalDateTime;

/** List-row view of a ticket. */
public record TicketSummary(
        String id,
        long number,
        String type,
        String status,
        String priority,
        String impact,
        String urgency,
        String title,
        String requesterId,
        String assigneeId,
        String parentId,
        String mergedIntoId,
        String source,
        LocalDateTime createdAt,
        LocalDateTime updatedAt,
        LocalDateTime firstResponseAt,
        LocalDateTime resolvedAt,
        LocalDateTime closedAt
) {
    public static TicketSummary of(Ticket ticket) {
        return new TicketSummary(
                ticket.getId(),
                ticket.getNumber(),
                ticket.getType(),
                ticket.getStatus(),
                ticket.getPriority(),
                ticket.getImpact(),
                ticket.getUrgency(),
                ticket.getTitle(),
                ticket.getRequesterId(),
                ticket.getAssigneeId(),
                ticket.getParentId(),
                ticket.getMergedIntoId(),
                ticket.getSource(),
                ticket.getCreatedAt(),
                ticket.getUpdatedAt(),
                ticket.getFirstResponseAt(),
                ticket.getResolvedAt(),
                ticket.getClosedAt()
        );
    }
}
