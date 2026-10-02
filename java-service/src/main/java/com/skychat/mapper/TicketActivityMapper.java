package com.skychat.mapper;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

/**
 * The append-only lifecycle history. No update or delete method exists.
 */
@Mapper
public interface TicketActivityMapper {
    String COLUMNS = "id, ticket_id, actor_id, activity_type, from_value, to_value, detail, "
            + "created_at";

    /**
     * {@code detail} is a JSON string rather than a map here: MyBatis needs an explicit
     * JSONB cast for the column, and doing the serialisation in the service keeps the mapper
     * free of Jackson concerns.
     */
    @Insert("""
        INSERT INTO ticket_activity (
            id, ticket_id, actor_id, activity_type, from_value, to_value, detail, created_at
        ) VALUES (
            #{id}, #{ticketId}, #{actorId}, #{activityType}, #{fromValue}, #{toValue},
            #{detailJson}::jsonb, #{createdAt}
        )
    """)
    int insert(
            @Param("id") String id,
            @Param("ticketId") String ticketId,
            @Param("actorId") String actorId,
            @Param("activityType") String activityType,
            @Param("fromValue") String fromValue,
            @Param("toValue") String toValue,
            @Param("detailJson") String detailJson,
            @Param("createdAt") java.time.LocalDateTime createdAt
    );

    @Select("""
        SELECT id, ticket_id, actor_id, activity_type, from_value, to_value,
               detail::text AS detail_json, created_at
        FROM ticket_activity
        WHERE ticket_id = #{ticketId}
        ORDER BY created_at ASC, id ASC
        LIMIT #{limit}
    """)
    List<TicketActivityRow> findByTicket(
            @Param("ticketId") String ticketId,
            @Param("limit") int limit
    );

    /** Re-parents the history when two tickets are merged. */
    @Update("UPDATE ticket_activity SET ticket_id = #{targetId} WHERE ticket_id = #{sourceId}")
    int moveToTicket(@Param("sourceId") String sourceId, @Param("targetId") String targetId);
}
