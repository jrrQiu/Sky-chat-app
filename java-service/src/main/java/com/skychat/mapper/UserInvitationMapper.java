package com.skychat.mapper;

import com.skychat.domain.UserInvitation;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface UserInvitationMapper {
    String COLUMNS = "id, email, name, roles, token_hash, invited_by, expires_at, "
            + "accepted_at, accepted_by, revoked_at, created_at";

    @Insert("""
        INSERT INTO user_invitation (
            id, email, name, roles, token_hash, invited_by, expires_at,
            accepted_at, accepted_by, revoked_at, created_at
        ) VALUES (
            #{id}, #{email}, #{name}, #{roles}, #{tokenHash}, #{invitedBy}, #{expiresAt},
            #{acceptedAt}, #{acceptedBy}, #{revokedAt}, #{createdAt}
        )
    """)
    int insert(UserInvitation invitation);

    @Select("""
        SELECT id, email, name, roles, token_hash, invited_by, expires_at,
               accepted_at, accepted_by, revoked_at, created_at
        FROM user_invitation
        WHERE token_hash = #{tokenHash}
    """)
    UserInvitation findByTokenHash(@Param("tokenHash") String tokenHash);

    @Select("""
        SELECT id, email, name, roles, token_hash, invited_by, expires_at,
               accepted_at, accepted_by, revoked_at, created_at
        FROM user_invitation
        WHERE id = #{id}
    """)
    UserInvitation findById(@Param("id") String id);

    /** Newest first; the status filter is derived state so it is applied per state. */
    @Select("""
        <script>
        SELECT id, email, name, roles, token_hash, invited_by, expires_at,
               accepted_at, accepted_by, revoked_at, created_at
        FROM user_invitation
        <where>
            <if test="status == 'pending'">
                accepted_at IS NULL AND revoked_at IS NULL AND expires_at &gt; NOW()
            </if>
            <if test="status == 'accepted'">
                accepted_at IS NOT NULL
            </if>
            <if test="status == 'revoked'">
                revoked_at IS NOT NULL
            </if>
            <if test="status == 'expired'">
                accepted_at IS NULL AND revoked_at IS NULL AND expires_at &lt;= NOW()
            </if>
        </where>
        ORDER BY created_at DESC
        LIMIT #{limit}
        </script>
    """)
    List<UserInvitation> list(
            @Param("status") String status,
            @Param("limit") int limit
    );

    /** An invite for the same address that is still usable. */
    @Select("""
        SELECT id, email, name, roles, token_hash, invited_by, expires_at,
               accepted_at, accepted_by, revoked_at, created_at
        FROM user_invitation
        WHERE email = #{email}
          AND accepted_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT #{limit}
    """)
    List<UserInvitation> findPendingByEmail(
            @Param("email") String email,
            @Param("limit") int limit
    );

    /**
     * Consumes the invitation. The {@code accepted_at IS NULL} predicate is what makes
     * acceptance single-use: two concurrent requests cannot both win.
     */
    @Update("""
        UPDATE user_invitation
        SET accepted_at = #{acceptedAt}, accepted_by = #{acceptedBy}
        WHERE id = #{id}
          AND accepted_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > #{acceptedAt}
    """)
    int accept(
            @Param("id") String id,
            @Param("acceptedAt") LocalDateTime acceptedAt,
            @Param("acceptedBy") String acceptedBy
    );

    @Update("""
        UPDATE user_invitation
        SET revoked_at = #{revokedAt}
        WHERE id = #{id}
          AND accepted_at IS NULL
          AND revoked_at IS NULL
    """)
    int revoke(@Param("id") String id, @Param("revokedAt") LocalDateTime revokedAt);
}
