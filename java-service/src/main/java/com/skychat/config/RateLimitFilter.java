package com.skychat.config;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.service.ratelimit.RateLimitResult;
import com.skychat.service.ratelimit.RateLimiter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Optional;

/**
 * Fixed-window rate limiting for the endpoints that are worth abusing.
 *
 * <p>Runs after {@link JwtAuthFilter} (see its {@code @Order}) so the authenticated
 * {@code X-User-ID} is already available, and before the controllers so a throttled request
 * never reaches the database.</p>
 *
 * <p>Defaults, all overridable under {@code skychat.rate-limit.*}:</p>
 * <ul>
 *   <li>{@code POST /v1/auth/login}: 10 per 5 min per IP <em>and</em> 5 per 5 min per email</li>
 *   <li>{@code POST /v1/auth/register}: 5 per hour per IP</li>
 *   <li>{@code POST /v1/chat/stream} and {@code /v1/agent/chat/stream}: 60 per min per user</li>
 *   <li>{@code PATCH /v1/approvals/{id}/decision}: 30 per min per user</li>
 * </ul>
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 100)
public class RateLimitFilter implements WebFilter {
    private static final Logger log = LoggerFactory.getLogger(RateLimitFilter.class);

    private static final long MINUTE = 60;
    private static final long FIVE_MINUTES = 300;
    private static final long HOUR = 3600;

    /**
     * The per-request marker that lets the deferred controller call know the exchange was
     * already rejected, without threading a value through the reactive chain.
     */
    private static final String LIMITED_ATTRIBUTE = RateLimitFilter.class.getName() + ".limited";

    /**
     * The login email cached by this filter, shared with {@code AuthController} so the request
     * body is parsed once.
     */
    static final String LOGIN_EMAIL_ATTRIBUTE = RateLimitFilter.class.getName() + ".loginEmail";

    private final RateLimiter rateLimiter;
    private final ClientAddressResolver clientAddressResolver;
    private final RateLimitProperties properties;
    private final ObjectMapper objectMapper;

    public RateLimitFilter(
            RateLimiter rateLimiter,
            ClientAddressResolver clientAddressResolver,
            RateLimitProperties properties,
            ObjectMapper objectMapper
    ) {
        this.rateLimiter = rateLimiter;
        this.clientAddressResolver = clientAddressResolver;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        HttpMethod method = request.getMethod();
        String path = request.getPath().value();
        String clientIp = clientAddressResolver.resolve(request);

        if (method == HttpMethod.POST && "/v1/auth/login".equals(path)) {
            return login(exchange, chain, clientIp);
        }

        if (method == HttpMethod.POST && "/v1/auth/register".equals(path)) {
            return check(
                    "register-ip",
                    Optional.ofNullable(clientIp),
                    properties.register().perIp().limit(),
                    HOUR,
                    exchange
            ).then(proceedOrLimit(exchange, chain));
        }

        if (method == HttpMethod.POST
                && ("/v1/chat/stream".equals(path) || "/v1/agent/chat/stream".equals(path))) {
            return check(
                    "chat-user",
                    subject(exchange, clientIp),
                    properties.chat().perUser().limit(),
                    MINUTE,
                    exchange
            ).then(proceedOrLimit(exchange, chain));
        }

        if (method == HttpMethod.PATCH && isApprovalDecision(path)) {
            return check(
                    "approval-decision-user",
                    subject(exchange, clientIp),
                    properties.approvalDecision().perUser().limit(),
                    MINUTE,
                    exchange
            ).then(proceedOrLimit(exchange, chain));
        }

        return chain.filter(exchange);
    }

    /**
     * Login applies two independent windows: the per-IP one stops a password spray from a
     * single host, the per-email one stops a distributed attack against one account.
     *
     * <p>The email comes from the body, which is why this branch (and only this branch) reads
     * and replays the request body.</p>
     */
    private Mono<Void> login(ServerWebExchange exchange, WebFilterChain chain, String clientIp) {
        return CachedRequestBody.read(exchange).flatMap(raw -> {
            ServerWebExchange effective = raw
                    .map(body -> {
                        String email = emailOf(body);
                        ServerWebExchange replayed = CachedRequestBody.replay(exchange, body);
                        replayed.getAttributes().put(
                                LOGIN_EMAIL_ATTRIBUTE,
                                email == null ? "" : email
                        );
                        return replayed;
                    })
                    .orElse(exchange);

            return check(
                    "login-ip",
                    Optional.ofNullable(clientIp),
                    properties.login().perIp().limit(),
                    FIVE_MINUTES,
                    effective
            ).then(check(
                    "login-email",
                    Optional.ofNullable(emailAttribute(effective)),
                    properties.login().perEmail().limit(),
                    FIVE_MINUTES,
                    effective
            )).then(proceedOrLimit(effective, chain));
        });
    }

    private Mono<Void> proceedOrLimit(ServerWebExchange exchange, WebFilterChain chain) {
        return Mono.defer(() -> isLimited(exchange) ? Mono.empty() : chain.filter(exchange));
    }

    /**
     * Counts one hit when a subject exists. Without an identity there is nothing meaningful to
     * count, so the check fails open rather than funnelling every caller into one bucket.
     */
    private Mono<Void> check(
            String bucket,
            Optional<String> subject,
            int limit,
            long windowSeconds,
            ServerWebExchange exchange
    ) {
        String value = subject.map(String::trim).filter(text -> !text.isEmpty()).orElse(null);
        if (value == null) {
            return Mono.empty();
        }
        return rateLimiter.check(bucket, value, limit, windowSeconds)
                .doOnNext(result -> {
                    if (!result.allowed()) {
                        exchange.getAttributes().put(LIMITED_ATTRIBUTE, result);
                    }
                })
                .then();
    }

    static void markLimited(ServerWebExchange exchange, RateLimitResult result) {
        exchange.getAttributes().put(LIMITED_ATTRIBUTE, result);
    }

    static boolean isLimited(ServerWebExchange exchange) {
        return exchange.getAttributes().get(LIMITED_ATTRIBUTE) instanceof RateLimitResult;
    }

    static void writeLimited(ServerWebExchange exchange, RateLimitResult result) {
        exchange.getResponse().setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
        exchange.getResponse().getHeaders().set(
                HttpHeaders.CONTENT_TYPE,
                "application/json; charset=UTF-8"
        );
        exchange.getResponse().getHeaders().set(
                HttpHeaders.RETRY_AFTER,
                String.valueOf(result.retryAfterSeconds())
        );
        byte[] bytes = ("{\"error\":\"RATE_LIMITED\",\"retryAfterSeconds\":"
                + result.retryAfterSeconds() + "}").getBytes(StandardCharsets.UTF_8);
        DataBuffer buffer = exchange.getResponse().bufferFactory().wrap(bytes);
        exchange.getResponse().writeWith(Mono.just(buffer)).subscribe();
    }

    private boolean isApprovalDecision(String path) {
        return path.startsWith("/v1/approvals/") && path.endsWith("/decision");
    }

    /**
     * Falls back to the client address for the (rare) authenticated endpoint reached without a
     * resolved user, so the limiter still has a stable key instead of one shared bucket.
     */
    private Optional<String> subject(ServerWebExchange exchange, String clientIp) {
        String userId = exchange.getRequest().getHeaders().getFirst("X-User-ID");
        if (userId != null && !userId.isBlank()) {
            return Optional.of(userId);
        }
        return Optional.ofNullable(clientIp);
    }

    /**
     * Best-effort body peek: a malformed body is left to the controller so the client gets the
     * normal validation error instead of a rate-limit response.
     */
    private String emailOf(String body) {
        if (body == null || body.isBlank()) {
            return null;
        }
        try {
            JsonNode node = objectMapper.readTree(body);
            String email = node.path("email").asText("");
            return email.isBlank() ? null : email.trim().toLowerCase(Locale.ROOT);
        } catch (Exception error) {
            log.debug("Unable to read the login email for rate limiting: {}", error.toString());
            return null;
        }
    }

    private String emailAttribute(ServerWebExchange exchange) {
        Object value = exchange.getAttributes().get(LOGIN_EMAIL_ATTRIBUTE);
        if (!(value instanceof String email) || email.isBlank()) {
            return null;
        }
        return email;
    }
}
