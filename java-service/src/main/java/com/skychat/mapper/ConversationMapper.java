package com.skychat.mapper;

import com.skychat.domain.Conversation;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

@Mapper
public interface ConversationMapper {
    @Select("""
        SELECT id, title, user_id, created_at, updated_at, is_pinned AS pinned, pinned_at
        FROM conversation
        WHERE user_id = #{userId}
        ORDER BY is_pinned DESC, updated_at DESC
    """)
    List<Conversation> findByUserId(String userId);

    @Select("""
        SELECT id, title, user_id, created_at, updated_at, is_pinned AS pinned, pinned_at
        FROM conversation
        WHERE id = #{id} AND user_id = #{userId}
    """)
    Conversation findById(@Param("id") String id, @Param("userId") String userId);

    @Insert("""
        INSERT INTO conversation (id, title, user_id, created_at, updated_at, is_pinned, pinned_at)
        VALUES (#{id}, #{title}, #{userId}, #{createdAt}, #{updatedAt}, #{pinned}, #{pinnedAt})
    """)
    @Options(useGeneratedKeys = false)
    int insert(Conversation conversation);

    @Update("""
        UPDATE conversation
        SET title = #{title}, updated_at = #{updatedAt}
        WHERE id = #{id} AND user_id = #{userId}
    """)
    int updateTitle(
            @Param("id") String id,
            @Param("userId") String userId,
            @Param("title") String title,
            @Param("updatedAt") java.time.LocalDateTime updatedAt
    );

    @Update("""
        UPDATE conversation
        SET updated_at = #{updatedAt}
        WHERE id = #{id} AND user_id = #{userId}
    """)
    int touch(
            @Param("id") String id,
            @Param("userId") String userId,
            @Param("updatedAt") java.time.LocalDateTime updatedAt
    );

    @Delete("DELETE FROM conversation WHERE id = #{id} AND user_id = #{userId}")
    int delete(@Param("id") String id, @Param("userId") String userId);
}
