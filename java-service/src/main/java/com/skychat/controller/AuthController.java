package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.config.RateLimitProperties;
import com.skychat.domain.UserAccount;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.AuthService;
import com.skychat.service.PasswordPolicy;
import com.skychat.service.ratelimit.RateLimitResult;
import com.skychat.service.ratelimit.RateLimiter;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Authentication endpoints.
 *
 * <p>Security rules enforced here rather than in the whitelisted filter chain: self-registration
 * is off unless configured, passwords must satisfy {@link PasswordPolicy}, and the per-email
 * login window is applied before any password hash is computed.</p>
 */
@RestController
@RequestMapping("/v1/auth")
public class AuthController {
    private static final String LOGIN_EMAIL_BUCKET = "login-email";
    private static final long LOGIN_EMAIL_WINDOW_SECONDS = 300;

    private final AuthService authService;
    private final AuditService auditService;
    private final RateLimiter rateLimiter;
    private final RateLimitProperties rateLimitProperties;
    private final ClientAddressResolver clientAddressResolver;

    public AuthController(
            AuthService authService,
            AuditService auditService,
            RateLimiter rateLimiter,
            RateLimitProperties rateLimitProperties,
            ClientAddressResolver clientAddressResolver
    ) {
        this.authService = authService;
        this.auditService = auditService;
        this.rateLimiter = rateLimiter;
        this.rateLimitProperties = rateLimitProperties;
        this.clientAddressResolver = clientAddressResolver;
    }

    @PostMapping("/register")
    public Mono<ResponseEntity<Map<String, Object>>> register(
            ServerWebExchange exchange,
            @RequestBody Map<String, String> body
    ) {
        String sourceIp = sourceIp(exchange);
        String userAgent = userAgent(exchange);

        return authService.register(body.get("email"), body.get("password"), body.get("name"))
                .map(session -> {
                    auditService.record(
                            AuditService.Context.user(session.user().getId(), sourceIp, userAgent),
                            "auth.register",
                            "user",
                            session.user().getId(),
                            AuditService.OUTCOME_SUCCESS,
                            Map.of("email", session.user().getEmail())
                    );
                    return ResponseEntity.ok(toResponse(session));
                })
                .onErrorResume(error -> Mono.just(registerError(
                        error,
                        AuditService.Context.user(null, sourceIp, userAgent)
                )));
    }

    @PostMapping("/login")
    public Mono<ResponseEntity<Map<String, Object>>> login(
            ServerWebExchange exchange,
            @RequestBody Map<String, String> body
    ) {
        String normalizedEmail = normalizeEmail(body.get("email"));
        String sourceIp = sourceIp(exchange);
        String userAgent = userAgent(exchange);

        return enforceEmailWindow(normalizedEmail, sourceIp, userAgent)
                .flatMap(guard -> guard.rateLimited()
                        ? Mono.just(rateLimited(guard.retryAfterSeconds()))
                        : authService.login(normalizedEmail, body.get("password"))
                                .map(outcome -> loginResponse(
                                        outcome,
                                        normalizedEmail,
                                        sourceIp,
                                        userAgent
                                )));
    }

    @GetMapping("/me")
    public Mono<ResponseEntity<UserAccount>> me(@RequestHeader("X-User-ID") String userId) {
        return Mono.fromCallable(() -> authService.findById(userId))
                .subscribeOn(Schedulers.boundedElastic())
                .map(user -> user == null
                        ? ResponseEntity.notFound().<UserAccount>build()
                        : ResponseEntity.ok(user));
    }

    /**
     * The per-email half of the login limit, decided by the controller because the email lives
     * in the body: the {@link com.skychat.config.RateLimitFilter} only guarantees the per-IP
     * window. Runs before authentication, so a throttled account never reaches the hash.
     */
    private Mono<EmailWindow> enforceEmailWindow(String email, String sourceIp, String userAgent) {
        if (email == null) {
            return Mono.just(EmailWindow.open());
        }
        return rateLimiter
                .check(
                        LOGIN_EMAIL_BUCKET,
                        email,
                        rateLimitProperties.login().perEmail().limit(),
                        LOGIN_EMAIL_WINDOW_SECONDS
                )
                .map(result -> {
                    if (result.allowed()) {
                        return EmailWindow.open();
                    }
                    auditService.record(
                            AuditService.Context.user(null, sourceIp, userAgent),
                            "auth.login_locked",
                            "user",
                            email,
                            AuditService.OUTCOME_LOCKED,
                            Map.of("reason", "RATE_LIMITED")
                    );
                    return new EmailWindow(true, result.retryAfterSeconds());
                })
                .onErrorReturn(EmailWindow.open())
                .defaultIfEmpty(EmailWindow.open());
    }

    /**
     * Whether the per-email window already rejected the attempt.
     */
    private record EmailWindow(boolean rateLimited, long retryAfterSeconds) {
        static EmailWindow open() {
            return new EmailWindow(false, 0);
        }
    }

    private ResponseEntity<Map<String, Object>> loginResponse(
            AuthService.LoginOutcome outcome,
            String email,
            String sourceIp,
            String userAgent
    ) {
        if (outcome.authenticated()) {
            UserAccount user = outcome.session().user();
            auditService.record(
                    AuditService.Context.user(user.getId(), sourceIp, userAgent),
                    "auth.login",
                    "user",
                    user.getId(),
                    AuditService.OUTCOME_SUCCESS,
                    Map.of("email", email)
            );
            return ResponseEntity.ok(toResponse(outcome.session()));
        }

        if (outcome.locked()) {
            auditService.record(
                    AuditService.Context.user(null, sourceIp, userAgent),
                    "auth.login_locked",
                    "user",
                    email,
                    AuditService.OUTCOME_LOCKED,
                    Map.of("reason", AuthService.ACCOUNT_LOCKED)
            );
            return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                    .header(HttpHeaders.RETRY_AFTER, String.valueOf(outcome.retryAfterSeconds()))
                    .body(errorBody(AuthService.ACCOUNT_LOCKED, outcome.message()));
        }

        auditService.record(
                AuditService.Context.user(null, sourceIp, userAgent),
                "auth.login_failed",
                "user",
                email,
                AuditService.OUTCOME_FAILURE,
                Map.of("reason", outcome.code() == null ? AuthService.INVALID_CREDENTIALS : outcome.code())
        );
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(errorBody(
                outcome.code() == null ? AuthService.INVALID_CREDENTIALS : outcome.code(),
                outcome.message()
        ));
    }

    private ResponseEntity<Map<String, Object>> registerError(
            Throwable error,
            AuditService.Context context
    ) {
        String code = error instanceof AuthException authError ? authError.code() : "REGISTER_FAILED";
        auditService.record(
                context,
                "auth.register",
                "user",
                null,
                AuthService.SELF_REGISTRATION_DISABLED.equals(code)
                        ? AuditService.OUTCOME_DENIED
                        : AuditService.OUTCOME_FAILURE,
                Map.of("reason", code)
        );

        if (AuthService.SELF_REGISTRATION_DISABLED.equals(code)) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(errorBody(code, error.getMessage()));
        }
        return ResponseEntity.badRequest().body(errorBody(code, error.getMessage()));
    }

    private ResponseEntity<Map<String, Object>> rateLimited(long retryAfterSeconds) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", "RATE_LIMITED");
        body.put("retryAfterSeconds", retryAfterSeconds);
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(HttpHeaders.RETRY_AFTER, String.valueOf(retryAfterSeconds))
                .body(body);
    }

    private Map<String, Object> errorBody(String code, String message) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", code);
        body.put("message", message == null ? "请求失败，请稍后重试" : message);
        return body;
    }

    private Map<String, Object> toResponse(AuthService.AuthSession session) {
        UserAccount user = session.user();
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("token", session.token());
        Map<String, Object> userBody = new LinkedHashMap<>();
        userBody.put("id", user.getId());
        userBody.put("email", user.getEmail());
        userBody.put("name", user.getName());
        // Roles drive which approval actions the workspace offers; the server still
        // re-checks them on every decision, this is presentation only.
        userBody.put("roles", user.getRolesList());
        response.put("user", userBody);
        return response;
    }

    private String normalizeEmail(String email) {
        if (email == null || email.isBlank()) {
            return null;
        }
        return email.trim().toLowerCase(Locale.ROOT);
    }

    private String userAgent(ServerWebExchange exchange) {
        if (exchange == null) {
            return null;
        }
        String userAgent = exchange.getRequest().getHeaders().getFirst(HttpHeaders.USER_AGENT);
        return userAgent == null || userAgent.isBlank() ? null : userAgent;
    }

    /**
     * The exchange is optional so this controller stays unit-testable without one; the resolver
     * returns null rather than a shared placeholder address.
     */
    private String sourceIp(ServerWebExchange exchange) {
        return exchange == null
                ? null
                : clientAddressResolver.resolve(exchange.getRequest());
    }
}
