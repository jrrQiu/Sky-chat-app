package com.skychat.dto;

import com.skychat.domain.UserAccount;

import java.time.LocalDateTime;
import java.util.List;

/**
 * A member row as the admin console sees it.
 *
 * <p>Never includes {@code password_hash}: the mapping is explicit rather than a serialised
 * domain object, so a new sensitive column cannot leak into an API response by accident.</p>
 */
public record UserSummary(
        String id,
        String email,
        String name,
        List<String> roles,
        String status,
        LocalDateTime createdAt,
        LocalDateTime disabledAt
) {
    public static UserSummary of(UserAccount user) {
        return new UserSummary(
                user.getId(),
                user.getEmail(),
                user.getName(),
                user.getRolesList(),
                user.getStatus(),
                user.getCreatedAt(),
                user.getDisabledAt()
        );
    }
}
