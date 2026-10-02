package com.skychat.service;

import com.skychat.config.AuthProperties;
import com.skychat.config.SecurityProperties;
import com.skychat.domain.UserAccount;
import com.skychat.mapper.UserMapper;
import com.skychat.service.ratelimit.InMemoryRateLimitStore;
import com.skychat.service.ratelimit.RateLimiter;
import com.skychat.service.ratelimit.RedisRateLimitStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Registration gating, password policy enforcement and the login lockout.
 *
 * <p>Hand-written fakes throughout: Mockito's inline mock maker cannot attach on this JDK, and
 * the collaborators here are small enough that a fake reads better than a stub.</p>
 */
class AuthServiceTest {

    private static final String STRONG_PASSWORD = "Sk7#trail-Blue42";

    private FakeUserMapper userMapper;
    private AuthService authService;

    @BeforeEach
    void setUp() {
        userMapper = new FakeUserMapper();
        authService = buildService(false);
    }

    /**
     * A Redis-less limiter exercises exactly the documented fallback path and keeps these tests
     * free of a network dependency.
     */
    private AuthService buildService(boolean selfRegistrationEnabled) {
        return new AuthService(
                userMapper,
                new JwtService(
                        new AuthProperties("unit-test-user-secret", 3600),
                        new JwtSigner(new com.fasterxml.jackson.databind.ObjectMapper())
                ),
                new PasswordPolicy(),
                new SecurityProperties(
                        new SecurityProperties.Auth(selfRegistrationEnabled),
                        new SecurityProperties.Http(false),
                        new SecurityProperties.Audit(12)
                ),
                new RateLimiter(new RedisRateLimitStore(null), new InMemoryRateLimitStore())
        );
    }

    private void enableSelfRegistration() {
        authService = buildService(true);
    }

    // ------------------------------------------------------------------ fakes

    private static final class FakeUserMapper implements UserMapper {
        private final List<UserAccount> users = new ArrayList<>();

        @Override
        public UserAccount findByEmail(String email) {
            return users.stream()
                    .filter(user -> user.getEmail().equals(email))
                    .findFirst()
                    .orElse(null);
        }

        @Override
        public UserAccount findById(String id) {
            return users.stream()
                    .filter(user -> user.getId().equals(id))
                    .findFirst()
                    .orElse(null);
        }

        @Override
        public int insert(UserAccount user) {
            users.add(user);
            return 1;
        }
    }

    // ------------------------------------------------------------------ registration

    @Test
    @DisplayName("self-registration disabled: register is refused with SELF_REGISTRATION_DISABLED")
    void selfRegistrationDisabled() {
        assertThat(authService.selfRegistrationEnabled()).isFalse();

        assertThatThrownBy(() -> authService.register("new@example.com", STRONG_PASSWORD, "New")
                .block())
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AuthService.SELF_REGISTRATION_DISABLED));

        // Nothing may be persisted behind a refused registration.
        assertThat(userMapper.users).isEmpty();
    }

    @Test
    @DisplayName("self-registration enabled: a strong password creates the account and returns a token")
    void selfRegistrationEnabled() {
        enableSelfRegistration();

        AuthService.AuthSession session = authService
                .register("new@example.com", STRONG_PASSWORD, "New User")
                .block();

        assertThat(session).isNotNull();
        assertThat(session.user().getEmail()).isEqualTo("new@example.com");
        assertThat(session.user().getRoles()).isEqualTo("employee");
        assertThat(session.token()).isNotBlank();
        assertThat(userMapper.users).hasSize(1);
    }

    @Test
    @DisplayName("a weak password is refused with its policy code and no account is created")
    void weakPasswordIsRefused() {
        enableSelfRegistration();

        assertThatThrownBy(() -> authService.register("weak@example.com", "abcdefghijkl", "Weak")
                .block())
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(PasswordPolicy.TOO_WEAK));

        assertThatThrownBy(() -> authService.register("short@example.com", "Ab1", "Short")
                .block())
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(PasswordPolicy.TOO_SHORT));

        assertThat(userMapper.users).isEmpty();
    }

    @Test
    @DisplayName("a duplicate email is refused")
    void duplicateEmailIsRefused() {
        enableSelfRegistration();
        authService.register("dupe@example.com", STRONG_PASSWORD, "First").block();

        assertThatThrownBy(() -> authService.register("DUPE@example.com", STRONG_PASSWORD, "Second")
                .block())
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AuthService.EMAIL_ALREADY_REGISTERED));
    }

    // ------------------------------------------------------------------ login

    @Test
    @DisplayName("a correct password authenticates and returns a token")
    void loginSucceeds() {
        enableSelfRegistration();
        authService.register("login@example.com", STRONG_PASSWORD, "Login").block();

        AuthService.LoginOutcome outcome = authService.login("login@example.com", STRONG_PASSWORD).block();

        assertThat(outcome).isNotNull();
        assertThat(outcome.authenticated()).isTrue();
        assertThat(outcome.session().token()).isNotBlank();
    }

    @Test
    @DisplayName("a wrong password is a plain INVALID_CREDENTIALS failure, not a lockout")
    void loginFailure() {
        enableSelfRegistration();
        authService.register("login@example.com", STRONG_PASSWORD, "Login").block();

        AuthService.LoginOutcome outcome = authService.login("login@example.com", "WrongPass123!").block();

        assertThat(outcome).isNotNull();
        assertThat(outcome.authenticated()).isFalse();
        assertThat(outcome.code()).isEqualTo(AuthService.INVALID_CREDENTIALS);
        assertThat(outcome.locked()).isFalse();
    }

    @Test
    @DisplayName("5 failures lock the account for 15 minutes: even the right password is 429")
    void lockoutAfterFiveFailures() {
        enableSelfRegistration();
        authService.register("lock@example.com", STRONG_PASSWORD, "Lock").block();

        // Failures 1 to 4 are ordinary credential failures.
        for (int attempt = 1; attempt <= 4; attempt++) {
            AuthService.LoginOutcome outcome = authService
                    .login("lock@example.com", "WrongPass123!")
                    .block();
            assertThat(outcome.authenticated()).as("attempt %d is not yet locked", attempt).isFalse();
            assertThat(outcome.locked()).as("attempt %d is not yet locked", attempt).isFalse();
            assertThat(outcome.code()).isEqualTo(AuthService.INVALID_CREDENTIALS);
        }

        // The 5th failure trips the lock and is already reported as locked.
        AuthService.LoginOutcome fifth = authService.login("lock@example.com", "WrongPass123!").block();
        assertThat(fifth.locked()).isTrue();
        assertThat(fifth.code()).isEqualTo(AuthService.ACCOUNT_LOCKED);
        assertThat(fifth.retryAfterSeconds()).isBetween(1L, 900L);

        // The correct password is now refused for the remainder of the window.
        AuthService.LoginOutcome locked = authService.login("lock@example.com", STRONG_PASSWORD).block();
        assertThat(locked.authenticated()).isFalse();
        assertThat(locked.locked()).isTrue();
        assertThat(locked.code()).isEqualTo(AuthService.ACCOUNT_LOCKED);

        // Further attempts inside the window must not extend the lock past its fixed expiry.
        AuthService.LoginOutcome again = authService.login("lock@example.com", STRONG_PASSWORD).block();
        assertThat(again.retryAfterSeconds()).isLessThanOrEqualTo(fifth.retryAfterSeconds());
    }

    @Test
    @DisplayName("a successful login resets the failure counter")
    void successResetsTheCounter() {
        enableSelfRegistration();
        authService.register("reset@example.com", STRONG_PASSWORD, "Reset").block();

        for (int attempt = 0; attempt < 4; attempt++) {
            authService.login("reset@example.com", "WrongPass123!").block();
        }
        AuthService.LoginOutcome firstSuccess = authService
                .login("reset@example.com", STRONG_PASSWORD)
                .block();
        assertThat(firstSuccess.authenticated()).isTrue();

        // The window is clear again, so four more failures still must not lock the account.
        for (int attempt = 0; attempt < 4; attempt++) {
            AuthService.LoginOutcome outcome = authService
                    .login("reset@example.com", "WrongPass123!")
                    .block();
            assertThat(outcome.locked()).isFalse();
        }
        assertThat(authService.login("reset@example.com", STRONG_PASSWORD).block().authenticated())
                .isTrue();
    }

    @Test
    @DisplayName("an unknown email fails like a wrong password and is never locked out first")
    void unknownEmailBehavesLikeAWrongPassword() {
        AuthService.LoginOutcome outcome = authService.login("nobody@example.com", "WrongPass123!").block();

        assertThat(outcome).isNotNull();
        assertThat(outcome.authenticated()).isFalse();
        assertThat(outcome.code()).isEqualTo(AuthService.INVALID_CREDENTIALS);
    }

    @Test
    @DisplayName("accounts are stored with a PBKDF2 cost factor, not in clear text")
    void passwordIsHashed() {
        enableSelfRegistration();
        authService.register("hash@example.com", STRONG_PASSWORD, "Hash").block();

        UserAccount stored = userMapper.users.stream()
                .max(Comparator.comparing(UserAccount::getCreatedAt, Comparator.nullsFirst(Comparator.naturalOrder())))
                .orElseThrow();
        assertThat(stored.getPasswordHash()).startsWith("pbkdf2_sha256$");
        assertThat(stored.getPasswordHash()).doesNotContain(STRONG_PASSWORD);
        assertThat(stored.getCreatedAt()).isNotNull();
        assertThat(stored.getCreatedAt()).isBefore(LocalDateTime.now().plusSeconds(5));
    }
}
