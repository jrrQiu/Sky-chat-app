package com.skychat.config;

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

import java.nio.charset.StandardCharsets;

/**
 * Authenticates the end-user bearer token and surfaces the identity as {@code X-User-ID} and
 * {@code X-User-Roles} for downstream filters and controllers.
 *
 * <p>The order matters: the rate-limit filter keys on the authenticated user, so this must run
 * first.</p>
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class JwtAuthFilter implements WebFilter {
    private final JwtService jwtService;

    public JwtAuthFilter(JwtService jwtService) {
        this.jwtService = jwtService;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        if (exchange.getRequest().getMethod() == HttpMethod.OPTIONS
                || path.startsWith("/health")
                || "/v1/auth/register".equals(path)
                || "/v1/auth/login".equals(path)) {
            return chain.filter(exchange);
        }

        String authorization = exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return unauthorized(exchange);
        }

        try {
            JwtService.Claims claims = jwtService.verify(authorization.substring(7));
            ServerHttpRequest mutatedRequest = exchange.getRequest()
                    .mutate()
                    .header("X-User-ID", claims.userId())
                    .header("X-User-Roles", String.join(",", claims.roles()))
                    .build();
            return chain.filter(exchange.mutate().request(mutatedRequest).build());
        } catch (IllegalArgumentException error) {
            return unauthorized(exchange);
        }
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange) {
        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
        exchange.getResponse().getHeaders().set(
                HttpHeaders.CONTENT_TYPE,
                "application/json; charset=UTF-8"
        );
        byte[] bytes = "{\"error\":\"Unauthorized\"}".getBytes(StandardCharsets.UTF_8);
        DataBuffer buffer = exchange.getResponse().bufferFactory().wrap(bytes);
        return exchange.getResponse().writeWith(Mono.just(buffer));
    }
}
