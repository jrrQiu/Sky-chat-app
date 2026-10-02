package com.skychat.config;

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
                || "/v1/auth/invitations/preview".equals(path)) {
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
        return Mono.fromCallable(() -> userMapper.findById(userId))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(user -> {
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
                })
                // A deleted account yields a null from the mapper, and Mono.fromCallable turns
                // a null return into an EMPTY completion — so without this line the filter
                // would neither continue the chain nor write a response, and the caller would
                // get a status-less empty reply instead of a 401.
                .switchIfEmpty(Mono.defer(() -> unauthorized(exchange)));
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange) {
        return unauthorized(exchange, "Unauthorized");
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange, String code) {
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
