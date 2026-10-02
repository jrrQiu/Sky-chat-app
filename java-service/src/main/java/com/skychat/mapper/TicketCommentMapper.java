package com.skychat.mapper;

import com.skychat.domain.ticket.TicketComment;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

/**
 * The immutable communication log. There is deliberately no update or delete method.
 */
@Mapper
public interface TicketCommentMapper {
    String COLUMNS = "id, ticket_id, author_id, author_roles, visibility, body, created_at";

    @Insert("""
        INSERT INTO ticket_comment (
            id, ticket_id, author_id, author_roles, visibility, body, created_at
        ) VALUES (
            #{id}, #{ticketId}, #{authorId}, #{authorRoles}, #{visibility}, #{body}, #{createdAt}
        )
    """)
    int insert(TicketComment comment);

    @Select("SELECT " + COLUMNS + " FROM ticket_comment WHERE id = #{id}")
    TicketComment findById(@Param("id") String id);

    /**
     * Chronological comment list.
     *
     * <p>{@code includeInternal=false} is the requester view and is the mechanism that keeps
     * agent-only notes from leaking; the filter is in SQL so no caller can forget it after the
     * rows have already been loaded.</p>
     */
    @Select("""
        <script>
        SELECT id, ticket_id, author_id, author_roles, visibility, body, created_at
        FROM ticket_comment
        WHERE ticket_id = #{ticketId}
        <if test="includeInternal == false">AND visibility = 'public'</if>
        ORDER BY created_at ASC, id ASC
        </script>
    """)
    List<TicketComment> findByTicket(
            @Param("ticketId") String ticketId,
            @Param("includeInternal") boolean includeInternal
    );

    @Select("SELECT COUNT(*) FROM ticket_comment WHERE ticket_id = #{ticketId}")
    int countByTicket(@Param("ticketId") String ticketId);

    /**
     * Moves comments to another ticket when two tickets are merged. The activity rows move
     * separately; the communication log and the history are re-parented together so the
     * merged ticket's conversation stays intact.
     */
    @org.apache.ibatis.annotations.Update("""
        UPDATE ticket_comment SET ticket_id = #{targetId} WHERE ticket_id = #{sourceId}
    """)
    int moveToTicket(@Param("sourceId") String sourceId, @Param("targetId") String targetId);
}
