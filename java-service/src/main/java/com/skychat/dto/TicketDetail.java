package com.skychat.dto;

import com.skychat.domain.ticket.Ticket;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Full ticket view: the list-row fields plus the conversation, the timeline, the relations and
 * the children.
 *
 * <p>Flattened rather than composing {@link TicketSummary} because the API contract the front
 * end consumes is a single object; keeping it explicit also means a new sensitive column cannot
 * reach the response by accident.</p>
 */
public record TicketDetail(
        String id,
        long number,
        String type,
        String status,
        String priority,
        String impact,
        String urgency,
        String title,
        String description,
        String requesterId,
        String assigneeId,
        String parentId,
        String mergedIntoId,
        String source,
        String conversationId,
        String resolution,
        String createdBy,
        LocalDateTime createdAt,
        LocalDateTime updatedAt,
        LocalDateTime firstResponseAt,
        LocalDateTime resolvedAt,
        LocalDateTime closedAt,
        List<TicketCommentView> comments,
        List<TicketActivityView> activities,
        List<TicketLinkView> links,
        List<TicketSummary> children
) {
    public static TicketDetail of(
            Ticket ticket,
            List<TicketCommentView> comments,
            List<TicketActivityView> activities,
            List<TicketLinkView> links,
            List<TicketSummary> children
    ) {
        return new TicketDetail(
                ticket.getId(),
                ticket.getNumber(),
                ticket.getType(),
                ticket.getStatus(),
                ticket.getPriority(),
                ticket.getImpact(),
                ticket.getUrgency(),
                ticket.getTitle(),
                ticket.getDescription(),
                ticket.getRequesterId(),
                ticket.getAssigneeId(),
                ticket.getParentId(),
                ticket.getMergedIntoId(),
                ticket.getSource(),
                ticket.getConversationId(),
                ticket.getResolution(),
                ticket.getCreatedBy(),
                ticket.getCreatedAt(),
                ticket.getUpdatedAt(),
                ticket.getFirstResponseAt(),
                ticket.getResolvedAt(),
                ticket.getClosedAt(),
                comments,
                activities,
                links,
                children
        );
    }
}
