package com.skychat.mapper;

import com.skychat.domain.ConversationSummary;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface ConversationSummaryMapper {
    @Select("""
        SELECT conversation_id, version, summary, start_message_id, end_message_id,
               covered_from_message_id, covered_until_message_id,
               field_values::text AS field_values, token_count, created_at
        FROM conversation_summary
        WHERE conversation_id = #{conversationId}
        ORDER BY version DESC
        LIMIT 1
    """)
    ConversationSummary findLatest(String conversationId);

    @Insert("""
        INSERT INTO conversation_summary (
            conversation_id, version, summary, start_message_id, end_message_id,
            covered_from_message_id, covered_until_message_id, field_values,
            token_count, created_at
        ) VALUES (
            #{conversationId}, #{version}, #{summary}, #{startMessageId},
            #{endMessageId}, #{coveredFromMessageId}, #{coveredUntilMessageId},
            CAST(#{fieldValues} AS jsonb), #{tokenCount}, NOW()
        )
    """)
    int insert(ConversationSummary summary);

    /**
     * Insert a version only if that version number is still free.
     *
     * <p>Two concurrent turns on one conversation can both compute
     * {@code version + 1}. An unconditional insert would fail the second one with a
     * primary-key violation inside the streaming path; the conflict is a normal race,
     * not an error, so the second writer simply loses and the first summary stands.
     * Returns 0 when it lost the race.
     */
    @Insert("""
        INSERT INTO conversation_summary (
            conversation_id, version, summary, start_message_id, end_message_id,
            covered_from_message_id, covered_until_message_id, field_values,
            token_count, created_at
        ) VALUES (
            #{conversationId}, #{version}, #{summary}, #{startMessageId},
            #{endMessageId}, #{coveredFromMessageId}, #{coveredUntilMessageId},
            CAST(#{fieldValues} AS jsonb), #{tokenCount}, NOW()
        )
        ON CONFLICT (conversation_id, version) DO NOTHING
    """)
    int insertIfVersionFree(ConversationSummary summary);

    @Select("""
        SELECT COUNT(*) FROM conversation_summary
        WHERE conversation_id = #{conversationId}
    """)
    int countForConversation(@Param("conversationId") String conversationId);
}
