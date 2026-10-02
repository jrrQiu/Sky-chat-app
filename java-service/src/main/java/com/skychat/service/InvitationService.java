package com.skychat.service;

import com.skychat.config.SecurityProperties;
import com.skychat.domain.UserAccount;
import com.skychat.domain.UserInvitation;
import com.skychat.domain.UserRoles;
import com.skychat.mapper.UserInvitationMapper;
import com.skychat.mapper.UserMapper;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/**
 * Admin-provisioned onboarding: an administrator names the address and the roles, and
 * the invitee sets their own password through a one-time link.
 *
 * <p>This is what replaces open self-registration. The service is deliberately blocking;
 * callers wrap it in {@code Mono.fromCallable(...).subscribeOn(boundedElastic())} like the
 * rest of the JDBC-backed code, because the datasource is blocking and must never run on
 * the event loop.</p>
 */
@Service
public class InvitationService {
    public static final String INVITATION_INVALID = "INVITATION_INVALID";
    public static final String INVITATION_EXPIRED = "INVITATION_EXPIRED";
    public static final String INVITATION_ALREADY_ACCEPTED = "INVITATION_ALREADY_ACCEPTED";
    public static final String INVITATION_REVOKED = "INVITATION_REVOKED";
    public static final String INVALID_ROLE = "INVALID_ROLE";
    public static final String NOT_FOUND = "INVITATION_NOT_FOUND";

    private static final int MIN_TTL_HOURS = 1;
    private static final int MAX_TTL_HOURS = 720;

    /** A freshly issued invitation plus the raw token, which is shown exactly once. */
    public record IssuedInvitation(UserInvitation invitation, String rawToken) {
    }

    /** What the set-password page is allowed to learn before the token is consumed. */
    public record Preview(
            String email,
            String name,
            List<String> roles,
            LocalDateTime expiresAt,
            boolean valid,
            String reason
    ) {
    }

    private final UserInvitationMapper invitationMapper;
    private final UserMapper userMapper;
    private final AuthService authService;
    private final JwtService jwtService;
    private final InvitationTokens tokens;
    private final SecurityProperties properties;
    private final AdminAuthorizer authorizer;

    public InvitationService(
            UserInvitationMapper invitationMapper,
            UserMapper userMapper,
            AuthService authService,
            JwtService jwtService,
            InvitationTokens tokens,
            SecurityProperties properties,
            AdminAuthorizer authorizer
    ) {
        this.invitationMapper = invitationMapper;
        this.userMapper = userMapper;
        this.authService = authService;
        this.jwtService = jwtService;
        this.tokens = tokens;
        this.properties = properties;
        this.authorizer = authorizer;
    }

    /**
     * Gate for the invitation-management routes. Reading or revoking invitations reveals who
     * is being onboarded, so it needs the same authority as issuing one.
     */
    public void assertCanAdminister(List<String> actorRoles) {
        authorizer.requireUserAdmin(actorRoles);
    }

    /**
     * Issues an invitation.
     *
     * <p>Any earlier pending invitation for the same address is revoked first, so a resend
     * cannot leave two live links in circulation. An address that already has an account is
     * refused rather than silently invited.</p>
     */
    public IssuedInvitation create(
            String email,
            String name,
            List<String> roles,
            Integer ttlHours,
            String invitedBy
    ) {
        String normalizedEmail = authService.normalizeEmailOrThrow(email);
        if (userMapper.findByEmail(normalizedEmail) != null) {
            throw new AuthException(AuthService.EMAIL_ALREADY_REGISTERED, "该邮箱已经注册");
        }

        List<String> normalizedRoles;
        try {
            normalizedRoles = UserRoles.normalize(roles);
        } catch (IllegalArgumentException error) {
            throw new AuthException(INVALID_ROLE, "未知角色：" + error.getMessage());
        }

        LocalDateTime now = LocalDateTime.now();
        for (UserInvitation stale : invitationMapper.findPendingByEmail(normalizedEmail, 20)) {
            invitationMapper.revoke(stale.getId(), now);
        }

        String rawToken = tokens.issue();
        UserInvitation invitation = new UserInvitation();
        invitation.setId(UUID.randomUUID().toString());
        invitation.setEmail(normalizedEmail);
        invitation.setName(name == null || name.isBlank() ? null : name.trim());
        invitation.setRolesList(normalizedRoles);
        invitation.setTokenHash(tokens.hash(rawToken));
        invitation.setInvitedBy(invitedBy);
        invitation.setExpiresAt(now.plusHours(resolveTtlHours(ttlHours)));
        invitation.setCreatedAt(now);
        invitationMapper.insert(invitation);

        return new IssuedInvitation(invitation, rawToken);
    }

    private int resolveTtlHours(Integer requested) {
        int fallback = properties != null && properties.invite() != null
                ? properties.invite().ttlHours()
                : 72;
        int hours = requested == null ? fallback : requested;
        return Math.max(MIN_TTL_HOURS, Math.min(MAX_TTL_HOURS, hours));
    }

    /** The absolute link an administrator copies and sends. Never logged. */
    public String inviteUrl(String rawToken) {
        String base = properties != null && properties.invite() != null
                ? properties.invite().baseUrl()
                : "";
        String trimmed = base == null ? "" : base.trim();
        if (trimmed.isEmpty()) {
            trimmed = "/invite";
        }
        String separator = trimmed.contains("?") ? "&" : "?";
        return trimmed + separator + "token=" + rawToken;
    }

    /**
     * Read-only check used by the set-password page. Returns the address and roles so the
     * invitee can confirm the link belongs to them, and a machine-readable reason when it
     * does not.
     */
    public Preview preview(String rawToken) {
        UserInvitation invitation = findByRawToken(rawToken);
        if (invitation == null) {
            return new Preview(null, null, List.of(), null, false, INVITATION_INVALID);
        }
        String reason = unusableReason(invitation);
        return new Preview(
                invitation.getEmail(),
                invitation.getName(),
                invitation.getRolesList(),
                invitation.getExpiresAt(),
                reason == null,
                reason
        );
    }

    /**
     * Consumes the invitation and creates the account.
     *
     * <p>Ordering is deliberate: the account is created first and the invitation is consumed
     * second. The unique email constraint is what makes a replay harmless — if the process
     * dies after creating the account but before consuming the token, re-using the link
     * simply fails with {@code EMAIL_ALREADY_REGISTERED} instead of letting an attacker reset
     * the password of an existing account. If the consume step loses a race, the account we
     * just created is removed so no half-provisioned user survives.</p>
     */
    public AuthService.AuthSession accept(String rawToken, String password, String name) {
        UserInvitation invitation = findByRawToken(rawToken);
        if (invitation == null) {
            throw new AuthException(INVITATION_INVALID, "邀请链接无效");
        }
        String reason = unusableReason(invitation);
        if (reason != null) {
            throw new AuthException(reason, messageFor(reason));
        }

        String email = invitation.getEmail();
        UserAccount user = authService.createAccount(
                email,
                name != null && !name.isBlank() ? name : invitation.getName(),
                invitation.getRolesList(),
                password
        );

        int consumed = invitationMapper.accept(
                invitation.getId(),
                LocalDateTime.now(),
                user.getId()
        );
        if (consumed == 0) {
            // Someone revoked or consumed it between the read and the write.
            userMapper.deleteById(user.getId());
            throw new AuthException(INVITATION_ALREADY_ACCEPTED, messageFor(INVITATION_ALREADY_ACCEPTED));
        }

        return new AuthService.AuthSession(
                jwtService.createToken(user.getId(), user.getEmail(), user.getRolesList()),
                user
        );
    }

    public List<UserInvitation> list(String status, int limit) {
        return invitationMapper.list(
                status == null || status.isBlank() ? null : status.trim(),
                Math.max(1, Math.min(limit, 500))
        );
    }

    /** Revokes a pending invitation. An already-consumed one cannot be revoked. */
    public void revoke(String id) {
        if (invitationMapper.revoke(id, LocalDateTime.now()) == 0) {
            throw new AuthException(NOT_FOUND, "邀请不存在或已使用");
        }
    }

    public UserInvitation findById(String id) {
        return invitationMapper.findById(id);
    }

    private UserInvitation findByRawToken(String rawToken) {
        if (rawToken == null || rawToken.isBlank()) {
            return null;
        }
        return invitationMapper.findByTokenHash(tokens.hash(rawToken));
    }

    /** {@code null} when the invitation can still be used. */
    private String unusableReason(UserInvitation invitation) {
        if (invitation.getRevokedAt() != null) {
            return INVITATION_REVOKED;
        }
        if (invitation.getAcceptedAt() != null) {
            return INVITATION_ALREADY_ACCEPTED;
        }
        if (invitation.getExpiresAt() == null
                || invitation.getExpiresAt().isBefore(LocalDateTime.now())) {
            return INVITATION_EXPIRED;
        }
        return null;
    }

    private String messageFor(String reason) {
        return switch (reason) {
            case INVITATION_EXPIRED -> "邀请链接已过期，请联系管理员重新发送";
            case INVITATION_ALREADY_ACCEPTED -> "该邀请已被使用";
            case INVITATION_REVOKED -> "该邀请已被撤销";
            default -> "邀请链接无效";
        };
    }

    /** Kept for readability at call sites that express the TTL in days. */
    public static Duration defaultTtl() {
        return Duration.ofHours(72);
    }
}
