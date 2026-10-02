package com.skychat.mapper;

import com.skychat.domain.UserProfile;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface UserProfileMapper {
    @Select("""
        SELECT user_id,
               preferences::text AS preferences,
               facts::text AS facts,
               updated_at
        FROM user_profile
        WHERE user_id = #{userId}
    """)
    UserProfile findById(String userId);

    @Insert("""
        INSERT INTO user_profile (user_id, preferences, facts, updated_at)
        VALUES (#{userId}, #{preferences}::jsonb, #{facts}::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            preferences = EXCLUDED.preferences,
            facts = EXCLUDED.facts,
            updated_at = NOW()
    """)
    int upsert(
            @Param("userId") String userId,
            @Param("preferences") String preferences,
            @Param("facts") String facts
    );
}
