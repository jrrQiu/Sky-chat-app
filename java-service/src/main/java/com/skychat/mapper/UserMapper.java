package com.skychat.mapper;

import com.skychat.domain.UserAccount;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface UserMapper {
    @Select("""
        SELECT id, email, name, password_hash, roles, created_at
        FROM user_account
        WHERE email = #{email}
    """)
    UserAccount findByEmail(String email);

    @Select("""
        SELECT id, email, name, password_hash, roles, created_at
        FROM user_account
        WHERE id = #{id}
    """)
    UserAccount findById(String id);

    @Insert("""
        INSERT INTO user_account (id, email, name, password_hash, roles, created_at)
        VALUES (#{id}, #{email}, #{name}, #{passwordHash}, #{roles}, #{createdAt})
    """)
    int insert(UserAccount user);
}
