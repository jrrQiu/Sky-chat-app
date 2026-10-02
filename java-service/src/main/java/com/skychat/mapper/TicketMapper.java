package com.skychat.mapper;

import com.skychat.domain.ticket.Ticket;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Mapper
public interface TicketMapper {
    String COLUMNS = "id, number, tenant_id, type, status, priority, impact, urgency, title, "
            + "description, requester_id, assignee_id, parent_id, source, conversation_id, "
            + "merged_into_id, resolution, created_by, first_response_at, resolved_at, "
            + "closed_at, created_at, updated_at";

    /**
     * Allocates the human-readable number.
     *
     * <p>Done as its own statement rather than {@code RETURNING} or {@code currval} so the
     * value is explicit in the service and cannot depend on connection reuse.</p>
     */
    @Select("SELECT nextval('ticket_number_seq')")
    long nextNumber();

    @Insert("""
        INSERT INTO ticket (
            id, number, tenant_id, type, status, priority, impact, urgency, title,
            description, requester_id, assignee_id, parent_id, source, conversation_id,
            merged_into_id, resolution, created_by, first_response_at, resolved_at,
            closed_at, created_at, updated_at
        ) VALUES (
            #{id}, #{number}, #{tenantId}, #{type}, #{status}, #{priority}, #{impact},
            #{urgency}, #{title}, #{description}, #{requesterId}, #{assigneeId}, #{parentId},
            #{source}, #{conversationId}, #{mergedIntoId}, #{resolution}, #{createdBy},
            #{firstResponseAt}, #{resolvedAt}, #{closedAt}, #{createdAt}, #{updatedAt}
        )
    """)
    int insert(Ticket ticket);

    @Select("SELECT " + COLUMNS + " FROM ticket WHERE id = #{id}")
    Ticket findById(@Param("id") String id);

    @Select("SELECT " + COLUMNS + " FROM ticket WHERE number = #{number}")
    Ticket findByNumber(@Param("number") long number);

    /**
     * Filtered, paged list.
     *
     * <p>{@code visibleToRequester} is the security predicate: when it is set only that
     * person's tickets are returned, which is how a non-agent is prevented from reading
     * somebody else's queue. Merged tickets are excluded unless explicitly asked for, since a
     * merged ticket is a tombstone pointing at its target.</p>
     */
    @Select("""
        <script>
        SELECT id, number, tenant_id, type, status, priority, impact, urgency, title,
               description, requester_id, assignee_id, parent_id, source, conversation_id,
               merged_into_id, resolution, created_by, first_response_at, resolved_at,
               closed_at, created_at, updated_at
        FROM ticket
        <where>
            <if test="visibleToRequester != null">
                requester_id = #{visibleToRequester}
            </if>
            <if test="status != null and status != ''">AND status = #{status}</if>
            <if test="type != null and type != ''">AND type = #{type}</if>
            <if test="priority != null and priority != ''">AND priority = #{priority}</if>
            <if test="assigneeId != null and assigneeId != ''">AND assignee_id = #{assigneeId}</if>
            <if test="requesterId != null and requesterId != ''">AND requester_id = #{requesterId}</if>
            <if test="includeMerged == false">AND merged_into_id IS NULL</if>
            <if test="query != null and query != ''">
                AND (title ILIKE '%' || #{query} || '%'
                     OR description ILIKE '%' || #{query} || '%'
                     OR CAST(number AS TEXT) = #{query})
            </if>
        </where>
        ORDER BY updated_at DESC, number DESC
        LIMIT #{limit} OFFSET #{offset}
        </script>
    """)
    List<Ticket> search(
            @Param("query") String query,
            @Param("status") String status,
            @Param("type") String type,
            @Param("priority") String priority,
            @Param("assigneeId") String assigneeId,
            @Param("requesterId") String requesterId,
            @Param("visibleToRequester") String visibleToRequester,
            @Param("includeMerged") boolean includeMerged,
            @Param("limit") int limit,
            @Param("offset") int offset
    );

    @Select("""
        <script>
        SELECT COUNT(*) FROM ticket
        <where>
            <if test="visibleToRequester != null">
                requester_id = #{visibleToRequester}
            </if>
            <if test="status != null and status != ''">AND status = #{status}</if>
            <if test="type != null and type != ''">AND type = #{type}</if>
            <if test="priority != null and priority != ''">AND priority = #{priority}</if>
            <if test="assigneeId != null and assigneeId != ''">AND assignee_id = #{assigneeId}</if>
            <if test="requesterId != null and requesterId != ''">AND requester_id = #{requesterId}</if>
            <if test="includeMerged == false">AND merged_into_id IS NULL</if>
            <if test="query != null and query != ''">
                AND (title ILIKE '%' || #{query} || '%'
                     OR description ILIKE '%' || #{query} || '%'
                     OR CAST(number AS TEXT) = #{query})
            </if>
        </where>
        </script>
    """)
    int countSearch(
            @Param("query") String query,
            @Param("status") String status,
            @Param("type") String type,
            @Param("priority") String priority,
            @Param("assigneeId") String assigneeId,
            @Param("requesterId") String requesterId,
            @Param("visibleToRequester") String visibleToRequester,
            @Param("includeMerged") boolean includeMerged
    );

    /** Grouped counts for the toolbar badges, under the same visibility predicate. */
    @Select("""
        <script>
        SELECT status AS key, COUNT(*) AS total FROM ticket
        <where>
            <if test="visibleToRequester != null">requester_id = #{visibleToRequester}</if>
            AND merged_into_id IS NULL
        </where>
        GROUP BY status
        </script>
    """)
    List<Map<String, Object>> countByStatus(
            @Param("visibleToRequester") String visibleToRequester
    );

    @Select("""
        <script>
        SELECT priority AS key, COUNT(*) AS total FROM ticket
        <where>
            <if test="visibleToRequester != null">requester_id = #{visibleToRequester}</if>
            AND merged_into_id IS NULL
        </where>
        GROUP BY priority
        </script>
    """)
    List<Map<String, Object>> countByPriority(
            @Param("visibleToRequester") String visibleToRequester
    );

    @Select("SELECT " + COLUMNS + " FROM ticket WHERE parent_id = #{parentId} "
            + "ORDER BY created_at ASC")
    List<Ticket> findChildren(@Param("parentId") String parentId);

    /** Partial field update; only non-null arguments are written. */
    @Update("""
        <script>
        UPDATE ticket
        <set>
            <if test="title != null">title = #{title},</if>
            <if test="description != null">description = #{description},</if>
            <if test="type != null">type = #{type},</if>
            <if test="impact != null">impact = #{impact},</if>
            <if test="urgency != null">urgency = #{urgency},</if>
            <if test="priority != null">priority = #{priority},</if>
            <if test="assigneeId != null">assignee_id = #{assigneeId},</if>
            updated_at = #{updatedAt}
        </set>
        WHERE id = #{id}
        </script>
    """)
    int updateFields(
            @Param("id") String id,
            @Param("title") String title,
            @Param("description") String description,
            @Param("type") String type,
            @Param("impact") String impact,
            @Param("urgency") String urgency,
            @Param("priority") String priority,
            @Param("assigneeId") String assigneeId,
            @Param("updatedAt") LocalDateTime updatedAt
    );

    /**
     * Status write, including the SLA timestamps.
     *
     * <p>{@code null} timestamps are applied as-is rather than ignored, because leaving
     * {@code resolved} must clear {@code resolved_at} — an ignored null would leave a reopened
     * ticket looking resolved to the SLA clock.</p>
     */
    @Update("""
        UPDATE ticket
        SET status = #{status},
            resolution = #{resolution},
            first_response_at = COALESCE(first_response_at, #{firstResponseAt}),
            resolved_at = #{resolvedAt},
            closed_at = #{closedAt},
            updated_at = #{updatedAt}
        WHERE id = #{id}
    """)
    int updateStatus(
            @Param("id") String id,
            @Param("status") String status,
            @Param("resolution") String resolution,
            @Param("firstResponseAt") LocalDateTime firstResponseAt,
            @Param("resolvedAt") LocalDateTime resolvedAt,
            @Param("closedAt") LocalDateTime closedAt,
            @Param("updatedAt") LocalDateTime updatedAt
    );

    /** Records the first agent response without touching anything else. */
    @Update("""
        UPDATE ticket
        SET first_response_at = #{at}
        WHERE id = #{id} AND first_response_at IS NULL
    """)
    int markFirstResponse(@Param("id") String id, @Param("at") LocalDateTime at);

    @Update("UPDATE ticket SET updated_at = #{updatedAt} WHERE id = #{id}")
    int touch(@Param("id") String id, @Param("updatedAt") LocalDateTime updatedAt);

    @Update("""
        UPDATE ticket
        SET merged_into_id = #{mergedIntoId}, status = 'closed',
            closed_at = COALESCE(closed_at, #{closedAt}), updated_at = #{closedAt}
        WHERE id = #{id} AND merged_into_id IS NULL
    """)
    int markMerged(
            @Param("id") String id,
            @Param("mergedIntoId") String mergedIntoId,
            @Param("closedAt") LocalDateTime closedAt
    );
}
