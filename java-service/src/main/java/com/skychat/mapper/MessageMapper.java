package com.skychat.mapper;

import com.skychat.domain.Message;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface MessageMapper {
    @Select("""
        SELECT id, role, content, conversation_id, created_at
        FROM message
        WHERE conversation_id = #{conversationId}
        ORDER BY created_at ASC, id ASC
    """)
    List<Message> findByConversationId(String conversationId);

    @Insert("""
        INSERT INTO message (id, role, content, conversation_id, created_at)
        VALUES (#{id}, #{role}, #{content}, #{conversationId}, #{createdAt})
    """)
    int insert(Message message);

    @Delete("""
        <script>
        DELETE FROM message
        WHERE id IN
        <foreach collection="ids" item="id" open="(" separator="," close=")">
            #{id}
        </foreach>
        AND conversation_id IN (
            SELECT id FROM conversation WHERE user_id = #{userId}
        )
        </script>
    """)
    int deleteByIds(
            @Param("ids") java.util.List<String> ids,
            @Param("userId") String userId
    );
}
