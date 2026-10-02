package com.skychat.mapper;

import com.skychat.domain.Memory;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface MemoryMapper {
    @Select("""
        SELECT id, user_id, kind, content, metadata::text AS metadata,
               expires_at, created_at
        FROM memory
        WHERE user_id = #{userId}
          AND kind IN ('preference', 'semantic', 'episodic')
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at DESC
        LIMIT #{limit}
    """)
    List<Memory> findEligible(
            @Param("userId") String userId,
            @Param("limit") int limit
    );
}
