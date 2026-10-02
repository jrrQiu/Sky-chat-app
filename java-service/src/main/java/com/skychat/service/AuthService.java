package com.skychat.service;

import com.skychat.config.SecurityProperties;
import com.skychat.domain.UserAccount;
import com.skychat.domain.UserRoles;
import com.skychat.mapper.UserMapper;
import com.skychat.service.ratelimit.RateLimiter;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * Registration and login.
 *
 * <p>Login failures are counted per email in a 15 minute window; five failures lock the account
 * for the remainder of that window. The counter lives in Redis when it is available, so the lock
 * is shared across replicas, and falls back to an in-process counter otherwise (see
 * {@link RateLimiter} for the documented degradation).</p>
 *
 * <p>Both entry points run on {@code boundedElastic}: the hashing and the JDBC mapper are
 * blocking by nature and must not run on an event loop. The reactive wrappers only exist to
 * await the rate-limit backend, which is reactive.</p>
 */
@Service
public class AuthService {
    public record AuthSession(String token, UserAccount user) {
    }

    /**
     * Outcome of a login attempt. Failures carry a stable {@code code} and, when locked, the
     * seconds the caller has to wait.
     */
    public record LoginOutcome(
            boolean authenticated,
            AuthSession session,
            String code,
            String message,
            long retryAfterSeconds
    ) {
        public boolean locked() {
            return ACCOUNT_LOCKED.equals(code);
        }

        public static LoginOutcome success(AuthSession session) {
            return new LoginOutcome(true, session, null, null, 0);
        }

        public static LoginOutcome failure(String code, String message) {
            return new LoginOutcome(false, null, code, message, 0);
        }

        public static LoginOutcome locked(long retryAfterSeconds) {
            return new LoginOutcome(
                    false,
                    null,
                    ACCOUNT_LOCKED,
                    "账号已被临时锁定，请稍后重试",
                    Math.max(1, retryAfterSeconds)
            );
        }
    }

    public static final String SELF_REGISTRATION_DISABLED = "SELF_REGISTRATION_DISABLED";
    public static final String ACCOUNT_LOCKED = "ACCOUNT_LOCKED";
    public static final String ACCOUNT_DISABLED = "ACCOUNT_DISABLED";
    public static final String INVALID_CREDENTIALS = "INVALID_CREDENTIALS";
    public static final String EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED";
    /** Deliberately identical for an unknown email and a wrong password. */
    private static final String INVALID_CREDENTIALS_MESSAGE = "邮箱或密码错误";

    private static final int PBKDF2_ITERATIONS = 210_000;
    private static final int PBKDF2_KEY_BITS = 256;
    private static final int SALT_BYTES = 16;
    private static final Base64.Encoder B64 = Base64.getEncoder();
    private static final Base64.Decoder B64_DECODER = Base64.getDecoder();
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    /**
     * Login lockout: 5 failed attempts inside 15 minutes lock the account for the rest of the
     * window.
     *
     * <p>{@code RateLimiter.check} refuses the hit that reaches the limit, so the fifth failure
     * itself is already reported as a lockout and no sixth request is needed. {@code peek} asks
     * the different question "is the counter already at the limit", so it must use the same
     * threshold: probing one below locked the account after <em>four</em> failures and refused a
     * correct password earlier than the documented policy.</p>
     */
    private static final String LOGIN_FAILURE_BUCKET = "login-failure-email";
    private static final int LOGIN_FAILURE_LIMIT = 5;
    private static final int LOGIN_LOCK_PEEK_THRESHOLD = LOGIN_FAILURE_LIMIT;
    private static final long LOGIN_LOCKOUT_SECONDS = Duration.ofMinutes(15).toSeconds();

    /**
     * Verified when the email is unknown, so a missing account costs the same as a wrong
     * password and response timing cannot be used to enumerate accounts.
     */
    private static final String DUMMY_PASSWORD_HASH = buildDummyHash();

    private final UserMapper userMapper;
    private final JwtService jwtService;
    private final PasswordPolicy passwordPolicy;
    private final SecurityProperties securityProperties;
    private final RateLimiter rateLimiter;

    public AuthService(
            UserMapper userMapper,
            JwtService jwtService,
            PasswordPolicy passwordPolicy,
            SecurityProperties securityProperties,
            RateLimiter rateLimiter
    ) {
        this.userMapper = userMapper;
        this.jwtService = jwtService;
        this.passwordPolicy = passwordPolicy;
        this.securityProperties = securityProperties;
        this.rateLimiter = rateLimiter;
    }

    /**
     * Registers a new account.
     *
     * <p>Self-registration is off by default; the flag is read on every call so a config
     * refresh takes effect without a restart.</p>
     *
     * @throws AuthException when registration is disabled or the input is rejected
     */
    public Mono<AuthSession> register(String email, String password, String name) {
        if (!selfRegistrationEnabled()) {
            return Mono.error(new AuthException(
                    SELF_REGISTRATION_DISABLED,
                    "自助注册未开放，请联系管理员开通账号"
            ));
        }
        return Mono.fromCallable(() -> registerNow(email, password, name))
                .subscribeOn(Schedulers.boundedElastic());
    }

    private AuthSession registerNow(String email, String password, String name) {
        UserAccount user = createAccount(email, name, List.of(UserRoles.DEFAULT), password);
        return new AuthSession(
                jwtService.createToken(user.getId(), user.getEmail(), user.getRolesList()),
                user
        );
    }

    /**
     * Creates an account with explicit roles.
     *
     * <p>Shared by the (usually disabled) self-registration path, the admin console and the
     * invitation accept flow, so password hashing, the password policy and the duplicate-email
     * check can never drift between them. Roles are validated against
     * {@link UserRoles#normalize}.</p>
     *
     * @throws AuthException with {@code PASSWORD_*} or {@code EMAIL_ALREADY_REGISTERED}
     * @throws IllegalArgumentException when a requested role is not known
     */
    public UserAccount createAccount(
            String email,
            String name,
            List<String> roles,
            String password
    ) {
        String normalizedEmail = normalizeEmail(email);
        PasswordPolicy.Result policy = passwordPolicy.validate(password);
        if (!policy.accepted()) {
            throw new AuthException(policy.code(), policy.message());
        }
        List<String> normalizedRoles = UserRoles.normalize(roles);
        if (userMapper.findByEmail(normalizedEmail) != null) {
            throw new AuthException(EMAIL_ALREADY_REGISTERED, "该邮箱已经注册");
        }

        UserAccount user = new UserAccount();
        user.setId(UUID.randomUUID().toString());
        user.setEmail(normalizedEmail);
        user.setName(displayName(name, normalizedEmail));
        user.setPasswordHash(hashPassword(password));
        user.setRolesList(normalizedRoles);
        user.setStatus(UserAccount.STATUS_ACTIVE);
        user.setCreatedAt(LocalDateTime.now());
        userMapper.insert(user);
        return user;
    }

    /** Name fallback shared by every account-creation path. */
    public static String displayName(String name, String email) {
        if (name != null && !name.isBlank()) {
            return name.trim();
        }
        return email.split("@")[0];
    }

    /** Exposed so invitation and admin flows normalise addresses identically. */
    public String normalizeEmailOrThrow(String email) {
        return normalizeEmail(email);
    }

    /**
     * Attempts a login. Never signals an error: every failure mode is a {@link LoginOutcome} so
     * the caller can distinguish a locked account (429) from bad credentials (401) without
     * parsing messages.
     */
    public Mono<LoginOutcome> login(String email, String password) {
        return Mono.fromCallable(() -> {
                    try {
                        return normalizeEmail(email);
                    } catch (IllegalArgumentException error) {
                        return null;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(normalizedEmail -> {
                    if (normalizedEmail == null) {
                        return Mono.just(LoginOutcome.failure(INVALID_CREDENTIALS, "邮箱或密码错误"));
                    }
                    // Check the lock before hashing: a locked account must not keep burning
                    // PBKDF2 work, and the caller must not learn whether the account exists.
                    return peekLock(normalizedEmail).flatMap(lockState -> lockState.locked()
                            ? Mono.just(LoginOutcome.locked(lockState.retryAfterSeconds()))
                            : attempt(normalizedEmail, password));
                });
    }

    /**
     * A successful credential check, or the reason it failed.
     *
     * <p>A failure is represented by the absence of a session, never by a null {@code Mono}
     * item: Reactor forbids null items and {@code Mono.fromCallable} turns a null return into
     * an empty completion, which would silently skip the failure branch below.</p>
     */
    private record Authenticated(AuthSession session, String failureCode) {
        static Authenticated ok(AuthSession session) {
            return new Authenticated(session, null);
        }

        static Authenticated badCredentials() {
            return new Authenticated(null, INVALID_CREDENTIALS);
        }

        static Authenticated disabled() {
            return new Authenticated(null, ACCOUNT_DISABLED);
        }
    }

    private Mono<LoginOutcome> attempt(String normalizedEmail, String password) {
        return Mono.fromCallable(() -> {
                    UserAccount user = userMapper.findByEmail(normalizedEmail);
                    boolean passwordMatches = user == null
                            ? verifyPassword(password, DUMMY_PASSWORD_HASH)
                            : verifyPassword(password, user.getPasswordHash());

                    if (user == null || !passwordMatches) {
                        return Authenticated.badCredentials();
                    }
                    // Only reveal the account state to someone who proved the password,
                    // otherwise a disabled account would be an enumeration oracle.
                    if (user.isDisabled()) {
                        return Authenticated.disabled();
                    }
                    return Authenticated.ok(new AuthSession(
                            jwtService.createToken(user.getId(), user.getEmail(), user.getRolesList()),
                            user
                    ));
                })
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(result -> result.session() == null
                        ? denial(result.failureCode(), normalizedEmail)
                        : succeeded(result.session(), normalizedEmail));
    }

    /**
     * Correct credentials: clear the failure window so an honest typo spree does not lock
     * the user out later.
     */
    private Mono<LoginOutcome> succeeded(AuthSession session, String normalizedEmail) {
        return rateLimiter.reset(LOGIN_FAILURE_BUCKET, normalizedEmail)
                .onErrorResume(error -> Mono.empty())
                .thenReturn(LoginOutcome.success(session));
    }

    /**
     * A rejected attempt. A disabled account is not a credential failure, so it must not
     * advance the lockout counter — otherwise repeatedly poking a disabled account would
     * lock an account that is already unusable, and the counter would show up in the audit
     * log as failed logins.
     */
    private Mono<LoginOutcome> denial(String failureCode, String normalizedEmail) {
        if (ACCOUNT_DISABLED.equals(failureCode)) {
            return Mono.just(LoginOutcome.failure(ACCOUNT_DISABLED, "账号已停用，请联系管理员"));
        }
        return recordFailure(normalizedEmail);
    }

    public UserAccount findById(String userId) {
        return userMapper.findById(userId);
    }

    public boolean selfRegistrationEnabled() {
        return securityProperties != null
                && securityProperties.auth() != null
                && securityProperties.auth().selfRegistrationEnabled();
    }

    private record LockState(boolean locked, long retryAfterSeconds) {
        static LockState open() {
            return new LockState(false, 0);
        }
    }

    private Mono<LockState> peekLock(String email) {
        return rateLimiter
                .peek(LOGIN_FAILURE_BUCKET, email, LOGIN_LOCK_PEEK_THRESHOLD, LOGIN_LOCKOUT_SECONDS)
                .map(result -> new LockState(!result.allowed(), result.retryAfterSeconds()))
                .onErrorReturn(LockState.open())
                .defaultIfEmpty(LockState.open());
    }

    /**
     * Counts one failed attempt and reports whether that attempt locked the account. Further
     * attempts inside the window do not extend it, so the lock lasts a bounded 15 minutes.
     *
     * <p>{@code check} tolerates exactly {@code limit} hits, so the fifth failure is the one that
     * <em>reaches</em> the limit rather than the one that exceeds it. "Did this attempt lock the
     * account" is therefore answered with the same threshold {@code peek} uses, which keeps the
     * lockout, the pre-flight check and the documented five-failure policy in agreement.</p>
     */
    private Mono<LoginOutcome> recordFailure(String email) {
        return rateLimiter
                .check(LOGIN_FAILURE_BUCKET, email, LOGIN_FAILURE_LIMIT, LOGIN_LOCKOUT_SECONDS)
                .flatMap(result -> result.allowed()
                        ? reachedLockThreshold(email, result.retryAfterSeconds())
                        : Mono.just(LoginOutcome.locked(result.retryAfterSeconds())))
                .onErrorReturn(LoginOutcome.failure(INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE))
                .defaultIfEmpty(
                        LoginOutcome.failure(INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE));
    }

    /** After a counted failure, is the counter now at the lockout threshold? */
    private Mono<LoginOutcome> reachedLockThreshold(String email, long retryAfterSeconds) {
        return rateLimiter
                .peek(
                        LOGIN_FAILURE_BUCKET,
                        email,
                        LOGIN_LOCK_PEEK_THRESHOLD,
                        LOGIN_LOCKOUT_SECONDS
                )
                .map(peeked -> peeked.allowed()
                        ? LoginOutcome.failure(INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE)
                        : LoginOutcome.locked(peeked.retryAfterSeconds() > 0
                                ? peeked.retryAfterSeconds()
                                : retryAfterSeconds))
                .onErrorReturn(LoginOutcome.failure(INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE))
                .defaultIfEmpty(
                        LoginOutcome.failure(INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE));
    }

    private String normalizeEmail(String email) {
        if (email == null || email.isBlank()) {
            throw new IllegalArgumentException("邮箱不能为空");
        }
        return email.trim().toLowerCase(Locale.ROOT);
    }

    private String hashPassword(String password) {
        byte[] salt = new byte[SALT_BYTES];
        SECURE_RANDOM.nextBytes(salt);
        byte[] hash = pbkdf2(password.toCharArray(), salt, PBKDF2_ITERATIONS);
        return "pbkdf2_sha256$"
                + PBKDF2_ITERATIONS + "$"
                + B64.encodeToString(salt) + "$"
                + B64.encodeToString(hash);
    }

    private boolean verifyPassword(String password, String encodedHash) {
        if (encodedHash == null) {
            return false;
        }

        String[] parts = encodedHash.split("\\$");
        if (parts.length != 4 || !"pbkdf2_sha256".equals(parts[0])) {
            return false;
        }

        try {
            int iterations = Integer.parseInt(parts[1]);
            byte[] salt = B64_DECODER.decode(parts[2]);
            byte[] expected = B64_DECODER.decode(parts[3]);
            byte[] actual = pbkdf2(
                    password == null ? new char[0] : password.toCharArray(),
                    salt,
                    iterations
            );
            return MessageDigest.isEqual(expected, actual);
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    /**
     * Hashes with a per-hash iteration count, so an existing weaker hash keeps verifying after
     * the cost factor is raised.
     */
    private byte[] pbkdf2(char[] password, byte[] salt, int iterations) {
        try {
            PBEKeySpec spec = new PBEKeySpec(password, salt, iterations, PBKDF2_KEY_BITS);
            SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
            return factory.generateSecret(spec).getEncoded();
        } catch (Exception error) {
            throw new IllegalStateException("Unable to hash password", error);
        }
    }

    private static String buildDummyHash() {
        byte[] salt = new byte[SALT_BYTES];
        SECURE_RANDOM.nextBytes(salt);
        try {
            PBEKeySpec spec = new PBEKeySpec(
                    "dummy-password-value".toCharArray(),
                    salt,
                    PBKDF2_ITERATIONS,
                    PBKDF2_KEY_BITS
            );
            byte[] hash = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                    .generateSecret(spec)
                    .getEncoded();
            return "pbkdf2_sha256$" + PBKDF2_ITERATIONS + "$"
                    + B64.encodeToString(salt) + "$" + B64.encodeToString(hash);
        } catch (Exception error) {
            throw new IllegalStateException("Unable to build the dummy password hash", error);
        }
    }
}
