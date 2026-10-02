package com.skychat.domain;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;

/**
 * A pending or consumed invitation.
 *
 * <p>Only the SHA-256 of the raw token is persisted, so reading the table cannot
 * produce a usable link. {@code acceptedAt} and {@code revokedAt} make the record
 * append-only in practice: the row is never deleted, so the audit trail survives.</p>
 */
public class UserInvitation {
    private String id;
    private String email;
    private String name;
    private String roles;
    private String tokenHash;
    private String invitedBy;
    private LocalDateTime expiresAt;
    private LocalDateTime acceptedAt;
    private String acceptedBy;
    private LocalDateTime revokedAt;
    private LocalDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getEmail() {
        return email;
    }

    public void setEmail(String email) {
        this.email = email;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getRoles() {
        return roles;
    }

    public void setRoles(String roles) {
        this.roles = roles;
    }

    public List<String> getRolesList() {
        if (roles == null || roles.isBlank()) {
            return List.of(UserRoles.DEFAULT);
        }
        return Arrays.stream(roles.split(","))
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .toList();
    }

    public void setRolesList(List<String> values) {
        this.roles = UserRoles.join(values);
    }

    public String getTokenHash() {
        return tokenHash;
    }

    public void setTokenHash(String tokenHash) {
        this.tokenHash = tokenHash;
    }

    public String getInvitedBy() {
        return invitedBy;
    }

    public void setInvitedBy(String invitedBy) {
        this.invitedBy = invitedBy;
    }

    public LocalDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(LocalDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public LocalDateTime getAcceptedAt() {
        return acceptedAt;
    }

    public void setAcceptedAt(LocalDateTime acceptedAt) {
        this.acceptedAt = acceptedAt;
    }

    public String getAcceptedBy() {
        return acceptedBy;
    }

    public void setAcceptedBy(String acceptedBy) {
        this.acceptedBy = acceptedBy;
    }

    public LocalDateTime getRevokedAt() {
        return revokedAt;
    }

    public void setRevokedAt(LocalDateTime revokedAt) {
        this.revokedAt = revokedAt;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    /** Derived state, computed server-side so every client agrees on it. */
    public String status() {
        if (revokedAt != null) {
            return "revoked";
        }
        if (acceptedAt != null) {
            return "accepted";
        }
        if (expiresAt != null && expiresAt.isBefore(LocalDateTime.now())) {
            return "expired";
        }
        return "pending";
    }
}
