package com.skychat.service;

import com.skychat.domain.UserRoles;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Central authorisation check for the admin surface.
 *
 * <p>Keeping it in one place matters because the failure mode of a duplicated check is
 * that one endpoint quietly forgets it. The caller's roles come from the signed JWT
 * ({@code X-User-Roles}, set by the auth filter), never from a client-supplied field.</p>
 */
@Component
public class AdminAuthorizer {
    public static final String FORBIDDEN_NOT_ADMIN = "FORBIDDEN_NOT_ADMIN";
    public static final String FORBIDDEN_ROLE_ESCALATION = "FORBIDDEN_ROLE_ESCALATION";

    /** Requires the ability to administer users at all. Returns the actor's roles. */
    public List<String> requireUserAdmin(List<String> actorRoles) {
        if (!UserRoles.canAdministerUsers(actorRoles)) {
            throw new AuthException(FORBIDDEN_NOT_ADMIN, "需要管理员权限");
        }
        return actorRoles;
    }

    /**
     * Requires the authority to grant the administrative roles themselves. Without this
     * a {@code user_admin} could promote itself to {@code admin} and the delegation
     * boundary would be decorative.
     */
    public void requireMayGrant(List<String> actorRoles, List<String> targetRoles) {
        if (!UserRoles.mayGrant(actorRoles, targetRoles)) {
            throw new AuthException(
                    FORBIDDEN_ROLE_ESCALATION,
                    "只有管理员可以授予管理员角色"
            );
        }
    }
}
