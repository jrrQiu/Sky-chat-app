package com.skychat.dto;

import java.time.LocalDateTime;
import java.util.Map;

/** One entry of the lifecycle timeline as the API exposes it. */
public record TicketActivityView(
        String id,
        String ticketId,
        String actorId,
        String activityType,
        String fromValue,
        String toValue,
        Map<String, Object> detail,
        LocalDateTime createdAt
) {
}
