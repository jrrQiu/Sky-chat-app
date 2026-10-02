package com.skychat.domain;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The single source of truth for which roles exist and who may administer users.
 *
 * <p>Exposing this list over {@code GET /v1/admin/roles} keeps the admin UI from
 * offering a role the server would reject, and the allow-list stops a typo from
 * silently creating a role nobody can authorise against.</p>
 */
public final class UserRoles {
    public static final String DEFAULT = "employee";
    public static final String ADMIN = "admin";
    /**
     * Delegated user administration: enough to onboard staff, not enough to change
     * platform configuration. Kept separate from {@code admin} so onboarding can be
     * handed to HR without handing over the whole system.
     */
    public static final String USER_ADMIN = "user_admin";

    /** Stable order: the admin UI renders the picker in exactly this sequence. */
    private static final List<String> ASSIGNABLE = List.of(
            DEFAULT,
            "it_user",
            "it_staff",
            "network_user",
            "network_admin",
            "hr_user",
            "hr_staff",
            "finance_user",
            "approver",
            "auditor",
            USER_ADMIN,
            ADMIN
    );

    private static final Set<String> KNOWN = new LinkedHashSet<>(ASSIGNABLE);

    private UserRoles() {
    }

    public static List<String> assignable() {
        return ASSIGNABLE;
    }

    public static boolean isKnown(String role) {
        return role != null && KNOWN.contains(role.trim().toLowerCase(Locale.ROOT));
    }

    /**
     * Normalises a requested role set: trims, lower-cases, de-duplicates and rejects
     * anything unknown. An empty result falls back to {@link #DEFAULT} rather than
     * producing an account with no roles at all.
     *
     * @throws IllegalArgumentException with the offending role when one is not known
     */
    public static List<String> normalize(List<String> requested) {
        if (requested == null || requested.isEmpty()) {
            return List.of(DEFAULT);
        }
        Set<String> result = new LinkedHashSet<>();
        for (String raw : requested) {
            if (raw == null || raw.isBlank()) {
                continue;
            }
            String role = raw.trim().toLowerCase(Locale.ROOT);
            if (!KNOWN.contains(role)) {
                throw new IllegalArgumentException(role);
            }
            result.add(role);
        }
        if (result.isEmpty()) {
            return List.of(DEFAULT);
        }
        // Preserve the canonical order so stored values compare cleanly.
        return ASSIGNABLE.stream().filter(result::contains).toList();
    }

    public static String join(List<String> roles) {
        return String.join(",", normalize(roles));
    }

    /**
     * Parses the {@code X-User-Roles} header form (comma separated, as issued by the JWT
     * filter) into a role list.
     */
    public static List<String> parseHeader(String header) {
        if (header == null || header.isBlank()) {
            return List.of();
        }
        return java.util.Arrays.stream(header.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .toList();
    }

    /** Whether the holder may administer users (and therefore reach /v1/admin/**). */
    public static boolean canAdministerUsers(List<String> roles) {
        if (roles == null) {
            return false;
        }
        return roles.contains(ADMIN) || roles.contains(USER_ADMIN);
    }

    /**
     * Only a full {@code admin} may grant or revoke the two administrative roles,
     * so a {@code user_admin} cannot escalate itself or anyone else.
     */
    public static boolean mayGrant(List<String> actorRoles, List<String> targetRoles) {
        if (targetRoles == null) {
            return true;
        }
        boolean grantsAdmin = targetRoles.contains(ADMIN) || targetRoles.contains(USER_ADMIN);
        if (!grantsAdmin) {
            return true;
        }
        return actorRoles != null && actorRoles.contains(ADMIN);
    }
}
