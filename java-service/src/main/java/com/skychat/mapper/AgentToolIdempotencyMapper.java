package com.skychat.mapper;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.time.LocalDateTime;

/**
 * Cross-service idempotency for agent tool calls (T3).
 *
 * <p>Keyed by {@code (tool_name, idem_key)} rather than by key alone: two different tools
 * may legitimately be handed the same key by a caller that derives it from a turn id, and
 * treating those as the same operation would return one tool's answer for another's
 * request.</p>
 *
 * <p>The response column stays a JSON string here rather than being mapped to a driver
 * object, the same choice {@code TicketActivityMapper} makes: {@code ::jsonb} on the way in,
 * {@code ::text} on the way out, so no driver-specific JSON type leaks into the domain.</p>
 */
@Mapper
public interface AgentToolIdempotencyMapper {

    @Select("""
        SELECT response::text AS response
        FROM agent_tool_idempotency
        WHERE tool_name = #{toolName} AND idem_key = #{idemKey}
    """)
    String findResponse(
            @Param("toolName") String toolName,
            @Param("idemKey") String idemKey
    );

    /**
     * Record the result of a tool call. Returns 0 when another request already recorded this
     * key, which is the signal that this caller lost the race and must not treat its own
     * result as authoritative.
     */
    @Insert("""
        INSERT INTO agent_tool_idempotency (tool_name, idem_key, response, actor_id, created_at)
        VALUES (#{toolName}, #{idemKey}, CAST(#{response} AS jsonb), #{actorId}, NOW())
        ON CONFLICT (tool_name, idem_key) DO NOTHING
    """)
    int insertIfAbsent(
            @Param("toolName") String toolName,
            @Param("idemKey") String idemKey,
            @Param("response") String response,
            @Param("actorId") String actorId
    );

    /**
     * Retention. The keys are only useful for as long as a duplicate delivery could plausibly
     * arrive, so rows older than the window are swept rather than kept forever.
     */
    @Delete("""
        DELETE FROM agent_tool_idempotency
        WHERE created_at < #{before}
    """)
    int deleteOlderThan(@Param("before") LocalDateTime before);

    @Select("SELECT COUNT(*) FROM agent_tool_idempotency")
    int count();
}
