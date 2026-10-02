package com.skychat.config;

import com.skychat.domain.UserAccount;
import com.skychat.mapper.UserMapper;
import com.skychat.service.JwtService;
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
import reactor.core.scheduler.Schedulers;

import java.nio.charset.StandardCharsets;
import java.util.Optional;

/**
 * Authenticates the end-user bearer token and surfaces the identity as {@code X-User-ID} and
 * {@code X-User-Roles} for downstream filters and controllers.
 *
 * <p>The order matters: the rate-limit filter keys on the authenticated user, so this must run
 * first.</p>
 *
 * <p>The account status is re-read on every request rather than trusted from the token. A JWT
 * cannot be withdrawn, so without this check disabling an account would only take effect when
 * its token expired — which would make the admin console's 停用 button a decoration. The read
 * is a primary-key lookup on a blocking datasource, so it is dispatched to
 * {@code boundedElastic}; if request volume ever makes that measurable, a short-TTL cache is
 * the next step, at the cost of a bounded revocation delay.</p>
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class JwtAuthFilter implements WebFilter {
    private final JwtService jwtService;
    private final UserMapper userMapper;

    public JwtAuthFilter(JwtService jwtService, UserMapper userMapper) {
        this.jwtService = jwtService;
        this.userMapper = userMapper;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        if (exchange.getRequest().getMethod() == HttpMethod.OPTIONS
                || path.startsWith("/health")
                || "/v1/auth/register".equals(path)
                || "/v1/auth/login".equals(path)
                // The invitee has no account yet, so these two must be reachable without a
                // token. They are matched exactly: a prefix match would also have opened
                // every sibling route under /v1/auth/invitations.
                || "/v1/auth/invitations/accept".equals(path)
                || "/v1/auth/invitations/preview".equals(path)
                // Server-to-server tool calls carry a token addressed to this service
                // (iss=agent-service, aud=java-service) rather than an end-user token, so the
                // user-token check below cannot apply. InternalToolController verifies that
                // credential itself, and requires both claims.
                || path.startsWith("/v1/internal/")) {
            return chain.filter(exchange);
        }

        String authorization = exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return unauthorized(exchange);
        }

        JwtService.Claims claims;
        try {
            claims = jwtService.verify(authorization.substring(7));
        } catch (IllegalArgumentException error) {
            return unauthorized(exchange);
        }

        String userId = claims.userId();
        // The lookup is wrapped in an Optional so "no such account" is a real value rather than
        // an empty completion. Reactor turns a null item into an empty Mono, and a
        // switchIfEmpty placed after the chain would then also fire on the chain's own empty
        // completion — i.e. after every response, when the headers are already committed.
        return Mono.fromCallable(() -> Optional.ofNullable(userMapper.findById(userId)))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(found -> {
                    if (found.isEmpty()) {
                        return unauthorized(exchange);
                    }
                    UserAccount user = found.get();
                    if (user.isDisabled()) {
                        return unauthorized(exchange, "ACCOUNT_DISABLED");
                    }
                    // The token authenticates the identity; the roles come from the database, so
                    // a role change (including a demotion) takes effect on the very next request
                    // instead of waiting for the token to expire — which could be a week.
                    ServerHttpRequest mutatedRequest = exchange.getRequest()
                            .mutate()
                            .header("X-User-ID", userId)
                            .header("X-User-Roles", String.join(",", user.getRolesList()))
                            .build();
                    return chain.filter(exchange.mutate().request(mutatedRequest).build());
                });
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange) {
        return unauthorized(exchange, "Unauthorized");
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange, String code) {
        // A streaming response commits its headers as soon as the first event is written, and a
        // committed response hands out a read-only header view — setting the status would throw
        // and tear the connection down. At that point the status cannot be changed any more, so
        // the only honest action left is to end the exchange.
        if (exchange.getResponse().isCommitted()) {
            return exchange.getResponse().setComplete();
        }
        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
        exchange.getResponse().getHeaders().set(
                HttpHeaders.CONTENT_TYPE,
                "application/json; charset=UTF-8"
        );
        byte[] bytes = ("{\"error\":\"" + code + "\"}").getBytes(StandardCharsets.UTF_8);
        DataBuffer buffer = exchange.getResponse().bufferFactory().wrap(bytes);
        return exchange.getResponse().writeWith(Mono.just(buffer));
    }
}
