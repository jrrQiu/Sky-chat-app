package com.skychat.service;

import com.skychat.domain.UserAccount;
import com.skychat.domain.UserRoles;
import com.skychat.mapper.UserMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Member management for the admin console.
 *
 * <p>Every mutation is authorised here rather than in the controller, and the guards that
 * matter are the ones that stop an administrator from destroying their own access:</p>
 * <ul>
 *   <li>a {@code user_admin} cannot grant the administrative roles at all;</li>
 *   <li>the last active administrator can be neither disabled nor demoted;</li>
 *   <li>nobody can disable their own account.</li>
 * </ul>
 */
@Service
public class UserAdminService {
    public static final String NOT_FOUND = "USER_NOT_FOUND";
    public static final String INVALID_ROLE = "INVALID_ROLE";
    public static final String LAST_ADMIN = "LAST_ADMIN_PROTECTED";
    public static final String CANNOT_DISABLE_SELF = "CANNOT_DISABLE_SELF";

    private static final int MAX_PAGE_SIZE = 100;

    /** One page of members plus the total, so the UI can page without a second call. */
    public record Page(List<UserAccount> items, int total, int page, int size) {
    }

    private final UserMapper userMapper;
    private final AdminAuthorizer authorizer;
    private final AuthService authService;

    public UserAdminService(
            UserMapper userMapper,
            AdminAuthorizer authorizer,
            AuthService authService
    ) {
        this.userMapper = userMapper;
        this.authorizer = authorizer;
        this.authService = authService;
    }

    public List<String> assignableRoles() {
        return UserRoles.assignable();
    }

    public Page search(
            List<String> actorRoles,
            String query,
            String status,
            int page,
            int size
    ) {
        authorizer.requireUserAdmin(actorRoles);
        int safeSize = Math.max(1, Math.min(size <= 0 ? 20 : size, MAX_PAGE_SIZE));
        int safePage = Math.max(0, page);
        String normalizedStatus = normalizeStatus(status);

        List<UserAccount> items = userMapper.search(
                blankToNull(query),
                normalizedStatus,
                safeSize,
                safePage * safeSize
        );
        int total = userMapper.countSearch(blankToNull(query), normalizedStatus);
        return new Page(items, total, safePage, safeSize);
    }

    /** Creates an account directly, with an initial password the admin hands over. */
    public UserAccount createUser(
            List<String> actorRoles,
            String email,
            String name,
            List<String> roles,
            String password
    ) {
        authorizer.requireUserAdmin(actorRoles);
        List<String> normalized = normalizeRoles(roles);
        authorizer.requireMayGrant(actorRoles, normalized);
        return authService.createAccount(email, name, normalized, password);
    }

    public UserAccount changeRoles(
            List<String> actorRoles,
            String targetUserId,
            List<String> roles
    ) {
        authorizer.requireUserAdmin(actorRoles);
        UserAccount target = requireUser(targetUserId);
        List<String> normalized = normalizeRoles(roles);
        // The actor must be allowed to grant the *new* set, and to remove the old one:
        // otherwise a user_admin could strip an admin's role.
        authorizer.requireMayGrant(actorRoles, normalized);
        authorizer.requireMayGrant(actorRoles, target.getRolesList());

        if (isLosingAdminAccess(target, normalized) && isLastAdministrator(target)) {
            throw new AuthException(LAST_ADMIN, "不能移除最后一个管理员的权限");
        }

        userMapper.updateRoles(target.getId(), UserRoles.join(normalized));
        return requireUser(targetUserId);
    }

    public UserAccount setEnabled(
            List<String> actorRoles,
            String targetUserId,
            boolean enabled,
            String actorUserId
    ) {
        authorizer.requireUserAdmin(actorRoles);
        UserAccount target = requireUser(targetUserId);

        if (!enabled && target.getId().equals(actorUserId)) {
            throw new AuthException(CANNOT_DISABLE_SELF, "不能停用自己的账号");
        }
        if (!enabled && target.getRolesList().contains(UserRoles.ADMIN)
                && isLastAdministrator(target)) {
            throw new AuthException(LAST_ADMIN, "不能停用最后一个管理员");
        }

        userMapper.updateStatus(
                target.getId(),
                enabled ? UserAccount.STATUS_ACTIVE : UserAccount.STATUS_DISABLED,
                enabled ? null : LocalDateTime.now()
        );
        return requireUser(targetUserId);
    }

    /**
     * Whether the change removes the account's administrative reach. True when it held an
     * administrative role and the new set does not.
     */
    private boolean isLosingAdminAccess(UserAccount target, List<String> newRoles) {
        return UserRoles.canAdministerUsers(target.getRolesList())
                && !UserRoles.canAdministerUsers(newRoles);
    }

    /**
     * Whether removing this account's administrative role would leave nobody able to run the
     * console.
     *
     * <p>The threshold depends on what is being removed: a {@code user_admin} only needs
     * another user administrator to remain, whereas removing {@code admin} requires another
     * full administrator, because a {@code user_admin} cannot grant administrative roles. Using
     * the combined count for both cases is what let the last real administrator be demoted
     * while a {@code user_admin} was present.</p>
     */
    private boolean isLastAdministrator(UserAccount target) {
        if (target.getRolesList().contains(UserRoles.ADMIN)) {
            return userMapper.countFullAdministrators() <= 1;
        }
        return userMapper.countAdministrators() <= 1;
    }

    private UserAccount requireUser(String id) {
        UserAccount user = userMapper.findById(id);
        if (user == null) {
            throw new AuthException(NOT_FOUND, "用户不存在");
        }
        return user;
    }

    private List<String> normalizeRoles(List<String> roles) {
        try {
            return UserRoles.normalize(roles);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_ROLE, "未知角色：" + error.getMessage());
        }
    }

    private String normalizeStatus(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        String value = status.trim().toLowerCase(java.util.Locale.ROOT);
        return switch (value) {
            case UserAccount.STATUS_ACTIVE, UserAccount.STATUS_DISABLED -> value;
            default -> null;
        };
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
