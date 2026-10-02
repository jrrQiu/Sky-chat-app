package com.skychat.domain;

import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;

public class UserAccount {
    public static final String STATUS_ACTIVE = "active";
    public static final String STATUS_DISABLED = "disabled";

    private String id;
    private String email;
    private String name;
    private String passwordHash;
    private String roles;
    private String status = STATUS_ACTIVE;
    private LocalDateTime disabledAt;
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

    public String getPasswordHash() {
        return passwordHash;
    }

    public void setPasswordHash(String passwordHash) {
        this.passwordHash = passwordHash;
    }

    public String getRoles() {
        return roles;
    }

    public void setRoles(String roles) {
        this.roles = roles;
    }

    public List<String> getRolesList() {
        if (roles == null || roles.isBlank()) {
            return List.of("employee");
        }
        return Arrays.stream(roles.split(","))
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .toList();
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public String getStatus() {
        return status == null ? STATUS_ACTIVE : status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public LocalDateTime getDisabledAt() {
        return disabledAt;
    }

    public void setDisabledAt(LocalDateTime disabledAt) {
        this.disabledAt = disabledAt;
    }

    public boolean isDisabled() {
        return STATUS_DISABLED.equalsIgnoreCase(getStatus());
    }

    /**
     * Role list with the stored string form as the source of truth. Kept next to
     * {@link #getRolesList()} so a role change can rebuild the column value without
     * a second parsing path.
     */
    public void setRolesList(List<String> values) {
        this.roles = values == null || values.isEmpty()
                ? UserRoles.DEFAULT
                : String.join(",", values);
    }
}
