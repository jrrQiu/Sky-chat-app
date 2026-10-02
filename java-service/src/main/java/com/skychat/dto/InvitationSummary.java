package com.skychat.dto;

import com.skychat.domain.UserInvitation;

import java.time.LocalDateTime;
import java.util.List;

/**
 * An invitation row as the admin console sees it.
 *
 * <p>{@code inviteUrl} is populated only in the response to the request that minted it: the
 * raw token exists in that response and nowhere else, so it cannot be re-read later.</p>
 */
public record InvitationSummary(
        String id,
        String email,
        String name,
        List<String> roles,
        String invitedBy,
        LocalDateTime expiresAt,
        LocalDateTime acceptedAt,
        boolean revoked,
        String status,
        String inviteUrl
) {
    public static InvitationSummary of(UserInvitation invitation) {
        return of(invitation, null);
    }

    public static InvitationSummary of(UserInvitation invitation, String inviteUrl) {
        return new InvitationSummary(
                invitation.getId(),
                invitation.getEmail(),
                invitation.getName(),
                invitation.getRolesList(),
                invitation.getInvitedBy(),
                invitation.getExpiresAt(),
                invitation.getAcceptedAt(),
                invitation.getRevokedAt() != null,
                invitation.status(),
                inviteUrl
        );
    }
}
