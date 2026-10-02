package com.skychat.mapper;

import com.skychat.domain.UserAccount;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface UserMapper {
    @Select("""
        SELECT id, email, name, password_hash, roles, status, disabled_at, created_at
        FROM user_account
        WHERE email = #{email}
    """)
    UserAccount findByEmail(String email);

    @Select("""
        SELECT id, email, name, password_hash, roles, status, disabled_at, created_at
        FROM user_account
        WHERE id = #{id}
    """)
    UserAccount findById(String id);

    @Insert("""
        INSERT INTO user_account (
            id, email, name, password_hash, roles, status, disabled_at, created_at
        ) VALUES (
            #{id}, #{email}, #{name}, #{passwordHash}, #{roles}, #{status},
            #{disabledAt}, #{createdAt}
        )
    """)
    int insert(UserAccount user);

    /**
     * Paged, filtered member list for the admin console. Filtering and paging happen
     * in SQL rather than in memory so the page stays correct as the table grows.
     */
    @Select("""
        <script>
        SELECT id, email, name, password_hash, roles, status, disabled_at, created_at
        FROM user_account
        <where>
            <if test="query != null and query != ''">
                (email ILIKE '%' || #{query} || '%' OR name ILIKE '%' || #{query} || '%')
            </if>
            <if test="status != null and status != ''">
                AND status = #{status}
            </if>
        </where>
        ORDER BY created_at DESC, email ASC
        LIMIT #{limit} OFFSET #{offset}
        </script>
    """)
    List<UserAccount> search(
            @Param("query") String query,
            @Param("status") String status,
            @Param("limit") int limit,
            @Param("offset") int offset
    );

    @Select("""
        <script>
        SELECT COUNT(*) FROM user_account
        <where>
            <if test="query != null and query != ''">
                (email ILIKE '%' || #{query} || '%' OR name ILIKE '%' || #{query} || '%')
            </if>
            <if test="status != null and status != ''">
                AND status = #{status}
            </if>
        </where>
        </script>
    """)
    int countSearch(@Param("query") String query, @Param("status") String status);

    @Update("""
        UPDATE user_account
        SET roles = #{roles}
        WHERE id = #{id}
    """)
    int updateRoles(@Param("id") String id, @Param("roles") String roles);

    @Update("""
        UPDATE user_account
        SET status = #{status}, disabled_at = #{disabledAt}
        WHERE id = #{id}
    """)
    int updateStatus(
            @Param("id") String id,
            @Param("status") String status,
            @Param("disabledAt") LocalDateTime disabledAt
    );

    /**
     * Compensating delete used only by the invitation accept path: if the account is
     * created but the invitation cannot be consumed, the account must not survive as
     * an orphan. Normal deactivation uses {@link #updateStatus}.
     */
    @Update("DELETE FROM user_account WHERE id = #{id}")
    int deleteById(@Param("id") String id);

    /**
     * Counts active accounts holding an administrative role. Used by the bootstrap
     * step and to refuse disabling or demoting the last administrator.
     * <p>The role column is CSV, so the match wraps it in commas and looks for an exact token.
     * A plain {@code LIKE '%admin%'} would also match {@code network_admin} and count people
     * who cannot administer anything.</p>
     */
    @Select("""
        SELECT COUNT(*) FROM user_account
        WHERE status = 'active'
          AND (
              ',' || roles || ',' LIKE '%,admin,%'
              OR ',' || roles || ',' LIKE '%,user_admin,%'
          )
    """)
    int countAdministrators();

    /**
     * Counts active accounts holding the full {@code admin} role specifically.
     *
     * <p>Needed because {@link #countAdministrators()} also counts {@code user_admin}, and a
     * {@code user_admin} cannot grant administrative roles. If the last full administrator
     * could be demoted while a {@code user_admin} remained, the deployment would be left with
     * nobody able to create an administrator — the exact lockout the guard exists to
     * prevent.</p>
     */
    @Select("""
        SELECT COUNT(*) FROM user_account
        WHERE status = 'active'
          AND ',' || roles || ',' LIKE '%,admin,%'
    """)
    int countFullAdministrators();
}
