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
               token_count, created_at
        FROM conversation_summary
        WHERE conversation_id = #{conversationId}
        ORDER BY version DESC
        LIMIT 1
    """)
    ConversationSummary findLatest(String conversationId);

    @Insert("""
        INSERT INTO conversation_summary (
            conversation_id, version, summary, start_message_id, end_message_id,
            token_count, created_at
        ) VALUES (
            #{conversationId}, #{version}, #{summary}, #{startMessageId},
            #{endMessageId}, #{tokenCount}, NOW()
        )
    """)
    int insert(ConversationSummary summary);
}
