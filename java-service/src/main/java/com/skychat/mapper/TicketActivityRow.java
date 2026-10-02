package com.skychat.mapper;

import java.time.LocalDateTime;

/**
 * Read model for one activity row.
 *
 * <p>{@code detailJson} stays a string here: PostgreSQL hands JSONB back as a driver object
 * that MyBatis cannot map to a {@code Map}, so the cast to text happens in SQL and the
 * conversion to a map happens in the service, where Jackson is already available. Keeping the
 * write-side {@code TicketActivity} free of a raw-JSON field is the reason this exists as a
 * separate type.</p>
 */
public record TicketActivityRow(
        String id,
        String ticketId,
        String actorId,
        String activityType,
        String fromValue,
        String toValue,
        String detailJson,
        LocalDateTime createdAt
) {
}
