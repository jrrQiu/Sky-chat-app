package com.skychat.service;

import com.skychat.config.SecurityProperties;
import com.skychat.domain.UserAccount;
import com.skychat.domain.UserInvitation;
import com.skychat.support.InMemoryUserInvitationMapper;
import com.skychat.support.InMemoryUserMapper;
import com.skychat.support.TestFixture;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The invitation flow — the onboarding path that replaces open self-registration.
 *
 * <p>The cases that matter most are the negative ones: a token must be single-use, must not
 * be replayable into a password reset, and must never let the invitee choose their own
 * roles.</p>
 */
class InvitationServiceTest {

    private static final String STRONG_PASSWORD = "Sk7#trail-Blue42";
    private static final String ADMIN = "admin-1";

    private InMemoryUserMapper userMapper;
    private InMemoryUserInvitationMapper invitationMapper;
    private SecurityProperties properties;
    private InvitationService service;

    @BeforeEach
    void setUp() {
        userMapper = new InMemoryUserMapper();
        invitationMapper = new InMemoryUserInvitationMapper();
        properties = TestFixture.securityProperties(false);
        service = new InvitationService(
                invitationMapper,
                userMapper,
                TestFixture.authService(userMapper, properties),
                TestFixture.jwtService(),
                new InvitationTokens(),
                properties,
                new AdminAuthorizer()
        );
    }

    private InvitationService.IssuedInvitation invite(String email, List<String> roles) {
        return service.create(email, "受邀者", roles, null, ADMIN);
    }

    // ------------------------------------------------------------------ issuing

    @Test
    @DisplayName("create issues a token, stores only its hash, and normalises the address")
    void createStoresOnlyTheHash() {
        InvitationService.IssuedInvitation issued = invite(" New.User@Example.COM ", List.of("it_staff"));

        assertThat(issued.rawToken()).isNotBlank();
        UserInvitation stored = invitationMapper.findById(issued.invitation().getId());
        assertThat(stored.getTokenHash()).isNotEqualTo(issued.rawToken());
        assertThat(stored.getTokenHash()).hasSize(64);
        assertThat(stored.getEmail()).isEqualTo("new.user@example.com");
        assertThat(stored.getRolesList()).containsExactly("it_staff");
        assertThat(stored.getExpiresAt()).isAfter(LocalDateTime.now());
    }

    @Test
    @DisplayName("create refuses an address that already has an account")
    void createRefusesExistingAccount() {
        userMapper.insert(account("taken@example.com"));

        assertThatThrownBy(() -> invite("taken@example.com", List.of("employee")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AuthService.EMAIL_ALREADY_REGISTERED));
    }

    @Test
    @DisplayName("create refuses an unknown role instead of storing a typo")
    void createRefusesUnknownRole() {
        assertThatThrownBy(() -> invite("new@example.com", List.of("superuser")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(InvitationService.INVALID_ROLE));
        assertThat(invitationMapper.invitations).isEmpty();
    }

    @Test
    @DisplayName("re-issuing for the same address revokes the previous link")
    void resendRevokesPreviousLink() {
        InvitationService.IssuedInvitation first = invite("new@example.com", List.of("employee"));
        InvitationService.IssuedInvitation second = invite("new@example.com", List.of("employee"));

        assertThat(second.rawToken()).isNotEqualTo(first.rawToken());
        assertThat(service.preview(first.rawToken()).valid()).isFalse();
        assertThat(service.preview(first.rawToken()).reason())
                .isEqualTo(InvitationService.INVITATION_REVOKED);
        assertThat(service.preview(second.rawToken()).valid()).isTrue();
    }

    @Test
    @DisplayName("ttlHours is clamped to a sane window")
    void ttlIsClamped() {
        InvitationService.IssuedInvitation longLived =
                service.create("a@example.com", null, List.of("employee"), 100_000, ADMIN);
        assertThat(longLived.invitation().getExpiresAt())
                .isBefore(LocalDateTime.now().plusHours(721));

        InvitationService.IssuedInvitation negative =
                service.create("b@example.com", null, List.of("employee"), -5, ADMIN);
        assertThat(negative.invitation().getExpiresAt())
                .isAfter(LocalDateTime.now().plusMinutes(59));
    }

    @Test
    @DisplayName("the invite URL carries the raw token and honours the configured base")
    void inviteUrlUsesConfiguredBase() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));

        String url = service.inviteUrl(issued.rawToken());
        assertThat(url).startsWith(TestFixture.INVITE_BASE_URL + "?token=");
        assertThat(url).endsWith(issued.rawToken());
    }

    // ------------------------------------------------------------------ preview

    @Test
    @DisplayName("preview reports the invited address and roles without consuming the token")
    void previewIsReadOnly() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("it_staff", "approver"));

        InvitationService.Preview preview = service.preview(issued.rawToken());
        assertThat(preview.valid()).isTrue();
        assertThat(preview.email()).isEqualTo("new@example.com");
        assertThat(preview.roles()).containsExactly("it_staff", "approver");
        assertThat(preview.reason()).isNull();
        // Still usable afterwards.
        assertThat(service.preview(issued.rawToken()).valid()).isTrue();
    }

    @Test
    @DisplayName("preview explains an unknown, expired, revoked or consumed token")
    void previewReasons() {
        assertThat(service.preview("not-a-token").reason())
                .isEqualTo(InvitationService.INVITATION_INVALID);
        assertThat(service.preview(null).reason())
                .isEqualTo(InvitationService.INVITATION_INVALID);

        InvitationService.IssuedInvitation expired = invite("expired@example.com", List.of("employee"));
        invitationMapper.findById(expired.invitation().getId())
                .setExpiresAt(LocalDateTime.now().minusMinutes(1));
        assertThat(service.preview(expired.rawToken()).reason())
                .isEqualTo(InvitationService.INVITATION_EXPIRED);

        InvitationService.IssuedInvitation revoked = invite("revoked@example.com", List.of("employee"));
        service.revoke(revoked.invitation().getId());
        assertThat(service.preview(revoked.rawToken()).reason())
                .isEqualTo(InvitationService.INVITATION_REVOKED);

        InvitationService.IssuedInvitation used = invite("used@example.com", List.of("employee"));
        service.accept(used.rawToken(), STRONG_PASSWORD, null);
        assertThat(service.preview(used.rawToken()).reason())
                .isEqualTo(InvitationService.INVITATION_ALREADY_ACCEPTED);
    }

    // ------------------------------------------------------------------ accepting

    @Test
    @DisplayName("accept creates the account with the invited roles and returns a session")
    void acceptCreatesAccount() {
        InvitationService.IssuedInvitation issued =
                invite("new@example.com", List.of("it_staff", "approver"));

        AuthService.AuthSession session = service.accept(issued.rawToken(), STRONG_PASSWORD, "新同事");

        assertThat(session.token()).isNotBlank();
        UserAccount created = userMapper.findByEmail("new@example.com");
        assertThat(created).isNotNull();
        assertThat(created.getName()).isEqualTo("新同事");
        assertThat(created.getRolesList()).containsExactlyInAnyOrder("it_staff", "approver");
        assertThat(created.getStatus()).isEqualTo(UserAccount.STATUS_ACTIVE);
        assertThat(created.getPasswordHash()).doesNotContain(STRONG_PASSWORD);
        // The token is consumed and cannot be reused.
        assertThat(invitationMapper.findById(issued.invitation().getId()).getAcceptedAt())
                .isNotNull();
    }

    @Test
    @DisplayName("accept cannot be replayed: the second attempt is refused and adds no user")
    void acceptIsSingleUse() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));
        service.accept(issued.rawToken(), STRONG_PASSWORD, null);

        assertThatThrownBy(() -> service.accept(issued.rawToken(), "Other7#Passphrase", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(InvitationService.INVITATION_ALREADY_ACCEPTED));
        assertThat(userMapper.users).hasSize(1);
    }

    /**
     * The property that closes the crash-during-accept window: if the account was created but
     * the token was never consumed, replaying the link must not become a password reset.
     */
    @Test
    @DisplayName("a token replayed after the account exists cannot reset its password")
    void replayAfterPartialFailureCannotResetPassword() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));
        // Simulate "account created, token not consumed".
        AuthService auth = TestFixture.authService(userMapper, properties);
        auth.createAccount("new@example.com", "先建好了", List.of("employee"), STRONG_PASSWORD);
        String originalHash = userMapper.findByEmail("new@example.com").getPasswordHash();

        assertThatThrownBy(() -> service.accept(issued.rawToken(), "Attacker7#Passphrase", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AuthService.EMAIL_ALREADY_REGISTERED));
        assertThat(userMapper.findByEmail("new@example.com").getPasswordHash())
                .isEqualTo(originalHash);
    }

    @Test
    @DisplayName("accept enforces the password policy")
    void acceptEnforcesPasswordPolicy() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));

        assertThatThrownBy(() -> service.accept(issued.rawToken(), "short1!", null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(PasswordPolicy.TOO_SHORT));
        assertThat(userMapper.users).isEmpty();
        // A rejected password must not burn the invitation.
        assertThat(service.preview(issued.rawToken()).valid()).isTrue();
    }

    @Test
    @DisplayName("accept refuses an expired or revoked token")
    void acceptRefusesUnusableTokens() {
        InvitationService.IssuedInvitation expired = invite("expired@example.com", List.of("employee"));
        invitationMapper.findById(expired.invitation().getId())
                .setExpiresAt(LocalDateTime.now().minusSeconds(1));
        assertThatThrownBy(() -> service.accept(expired.rawToken(), STRONG_PASSWORD, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(InvitationService.INVITATION_EXPIRED));

        InvitationService.IssuedInvitation revoked = invite("revoked@example.com", List.of("employee"));
        service.revoke(revoked.invitation().getId());
        assertThatThrownBy(() -> service.accept(revoked.rawToken(), STRONG_PASSWORD, null))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(InvitationService.INVITATION_REVOKED));

        assertThat(userMapper.users).isEmpty();
    }

    @Test
    @DisplayName("the invitee cannot choose their own roles: only the invitation's roles apply")
    void rolesComeFromTheInvitationOnly() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));

        service.accept(issued.rawToken(), STRONG_PASSWORD, null);

        assertThat(userMapper.findByEmail("new@example.com").getRolesList())
                .containsExactly("employee");
    }

    @Test
    @DisplayName("revoke refuses an already-consumed invitation")
    void revokeRefusesConsumedInvitation() {
        InvitationService.IssuedInvitation issued = invite("new@example.com", List.of("employee"));
        service.accept(issued.rawToken(), STRONG_PASSWORD, null);

        assertThatThrownBy(() -> service.revoke(issued.invitation().getId()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(InvitationService.NOT_FOUND));
    }

    @Test
    @DisplayName("list filters by derived status")
    void listFiltersByStatus() {
        InvitationService.IssuedInvitation pending = invite("pending@example.com", List.of("employee"));
        InvitationService.IssuedInvitation accepted = invite("accepted@example.com", List.of("employee"));
        service.accept(accepted.rawToken(), STRONG_PASSWORD, null);
        InvitationService.IssuedInvitation revoked = invite("revoked@example.com", List.of("employee"));
        service.revoke(revoked.invitation().getId());

        assertThat(service.list("pending", 50)).hasSize(1);
        assertThat(service.list("accepted", 50)).hasSize(1);
        assertThat(service.list("revoked", 50)).hasSize(1);
        assertThat(service.list(null, 50)).hasSize(3);

        // A reserved default keeps every caller honest about the token's status.
        assertThat(pending.invitation().status()).isEqualTo("pending");
    }

    private UserAccount account(String email) {
        UserAccount user = new UserAccount();
        user.setId("existing-" + email);
        user.setEmail(email);
        user.setName("Existing");
        user.setPasswordHash("hash");
        user.setRoles("employee");
        user.setStatus(UserAccount.STATUS_ACTIVE);
        user.setCreatedAt(LocalDateTime.now());
        return user;
    }
}
