package com.skychat.support;

import com.skychat.domain.UserInvitation;
import com.skychat.mapper.UserInvitationMapper;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * In-memory {@link UserInvitationMapper} mirroring the SQL predicates, in particular the
 * single-use consume: {@code accept} only succeeds when the row is still pending.
 */
public class InMemoryUserInvitationMapper implements UserInvitationMapper {
    public final List<UserInvitation> invitations = new ArrayList<>();

    @Override
    public int insert(UserInvitation invitation) {
        invitations.add(invitation);
        return 1;
    }

    @Override
    public UserInvitation findByTokenHash(String tokenHash) {
        return invitations.stream()
                .filter(row -> row.getTokenHash().equals(tokenHash))
                .findFirst()
                .orElse(null);
    }

    @Override
    public UserInvitation findById(String id) {
        return invitations.stream()
                .filter(row -> row.getId().equals(id))
                .findFirst()
                .orElse(null);
    }

    @Override
    public List<UserInvitation> list(String status, int limit) {
        List<UserInvitation> matched = invitations.stream()
                .filter(row -> status == null || status.equals(row.status()))
                .sorted(Comparator.comparing(UserInvitation::getCreatedAt,
                                Comparator.nullsLast(Comparator.reverseOrder())))
                .toList();
        return matched.size() > limit ? matched.subList(0, limit) : matched;
    }

    @Override
    public List<UserInvitation> findPendingByEmail(String email, int limit) {
        List<UserInvitation> matched = invitations.stream()
                .filter(row -> row.getEmail().equalsIgnoreCase(email))
                .filter(row -> "pending".equals(row.status()))
                .toList();
        return matched.size() > limit ? matched.subList(0, limit) : matched;
    }

    /** Single-use: the update only matches while the row is still pending and unexpired. */
    @Override
    public int accept(String id, LocalDateTime acceptedAt, String acceptedBy) {
        UserInvitation row = findById(id);
        if (row == null
                || row.getAcceptedAt() != null
                || row.getRevokedAt() != null
                || row.getExpiresAt() == null
                || !row.getExpiresAt().isAfter(acceptedAt)) {
            return 0;
        }
        row.setAcceptedAt(acceptedAt);
        row.setAcceptedBy(acceptedBy);
        return 1;
    }

    @Override
    public int revoke(String id, LocalDateTime revokedAt) {
        UserInvitation row = findById(id);
        if (row == null || row.getAcceptedAt() != null || row.getRevokedAt() != null) {
            return 0;
        }
        row.setRevokedAt(revokedAt);
        return 1;
    }
}
