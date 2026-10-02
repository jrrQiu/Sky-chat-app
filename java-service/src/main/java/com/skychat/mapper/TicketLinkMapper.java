package com.skychat.mapper;

import com.skychat.domain.ticket.TicketLink;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface TicketLinkMapper {
    String COLUMNS = "id, ticket_id, linked_ticket_id, link_type, created_by, created_at";

    @Insert("""
        INSERT INTO ticket_link (
            id, ticket_id, linked_ticket_id, link_type, created_by, created_at
        ) VALUES (
            #{id}, #{ticketId}, #{linkedTicketId}, #{linkType}, #{createdBy}, #{createdAt}
        )
    """)
    int insert(TicketLink link);

    @Select("SELECT " + COLUMNS + " FROM ticket_link WHERE id = #{id}")
    TicketLink findById(@Param("id") String id);

    /** Links declared by this ticket. */
    @Select("SELECT " + COLUMNS + " FROM ticket_link WHERE ticket_id = #{ticketId} "
            + "ORDER BY created_at ASC")
    List<TicketLink> findOutgoing(@Param("ticketId") String ticketId);

    /**
     * Links declared by other tickets that point at this one. Returned separately so the
     * service can derive the inverse label and mark the direction.
     */
    @Select("SELECT " + COLUMNS + " FROM ticket_link WHERE linked_ticket_id = #{ticketId} "
            + "ORDER BY created_at ASC")
    List<TicketLink> findIncoming(@Param("ticketId") String ticketId);

    @Select("""
        SELECT COUNT(*) FROM ticket_link
        WHERE ticket_id = #{ticketId} AND linked_ticket_id = #{linkedTicketId}
          AND link_type = #{linkType}
    """)
    int exists(
            @Param("ticketId") String ticketId,
            @Param("linkedTicketId") String linkedTicketId,
            @Param("linkType") String linkType
    );

    @Delete("DELETE FROM ticket_link WHERE id = #{id} AND ticket_id = #{ticketId}")
    int delete(@Param("id") String id, @Param("ticketId") String ticketId);

    /**
     * Whether a path of merges already leads from {@code candidate} back to {@code ticketId}.
     * Used to refuse a merge cycle (A into B while B is already merged into A).
     */
    @Select("""
        WITH RECURSIVE chain AS (
            SELECT id, merged_into_id FROM ticket WHERE id = #{ticketId}
            UNION ALL
            SELECT t.id, t.merged_into_id
            FROM ticket t JOIN chain c ON t.id = c.merged_into_id
        )
        SELECT COUNT(*) FROM chain WHERE id = #{candidateId}
    """)
    int mergeChainContains(
            @Param("ticketId") String ticketId,
            @Param("candidateId") String candidateId
    );

    @org.apache.ibatis.annotations.Update("""
        UPDATE ticket_link
        SET linked_ticket_id = #{targetId}
        WHERE linked_ticket_id = #{sourceId}
    """)
    int repointIncoming(@Param("sourceId") String sourceId, @Param("targetId") String targetId);

    @org.apache.ibatis.annotations.Update("""
        UPDATE ticket_link
        SET ticket_id = #{targetId}
        WHERE ticket_id = #{sourceId}
    """)
    int repointOutgoing(@Param("sourceId") String sourceId, @Param("targetId") String targetId);
}
