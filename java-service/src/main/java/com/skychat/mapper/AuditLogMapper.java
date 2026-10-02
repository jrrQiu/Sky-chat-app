package com.skychat.mapper;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;

@Mapper
public interface AuditLogMapper {
    /**
     * {@code detail} is bound as a JSON string and cast server-side: it keeps the domain free
     * of driver specific JSON types while still storing real JSONB.
     */
    @Insert("""
        INSERT INTO audit_log (
            id, occurred_at, actor_id, actor_type, action, object_type, object_id,
            outcome, source_ip, user_agent, detail
        ) VALUES (
            #{id}, #{occurredAt}, #{actorId}, #{actorType}, #{action}, #{objectType}, #{objectId},
            #{outcome}, #{sourceIp}, #{userAgent}, CAST(#{detailJson} AS jsonb)
        )
    """)
    int insert(
            @Param("id") String id,
            @Param("occurredAt") LocalDateTime occurredAt,
            @Param("actorId") String actorId,
            @Param("actorType") String actorType,
            @Param("action") String action,
            @Param("objectType") String objectType,
            @Param("objectId") String objectId,
            @Param("outcome") String outcome,
            @Param("sourceIp") String sourceIp,
            @Param("userAgent") String userAgent,
            @Param("detailJson") String detailJson
    );
}
