package com.skychat.dto;

import com.skychat.domain.ticket.TicketComment;

import java.time.LocalDateTime;

/** One entry of the communication log as the API exposes it. */
public record TicketCommentView(
        String id,
        String ticketId,
        String authorId,
        String authorRoles,
        String visibility,
        String body,
        LocalDateTime createdAt
) {
    public static TicketCommentView of(TicketComment comment) {
        return new TicketCommentView(
                comment.getId(),
                comment.getTicketId(),
                comment.getAuthorId(),
                comment.getAuthorRoles(),
                comment.getVisibility(),
                comment.getBody(),
                comment.getCreatedAt()
        );
    }
}
