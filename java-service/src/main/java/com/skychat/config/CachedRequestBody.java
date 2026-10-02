package com.skychat.config;

import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.core.io.buffer.DataBufferUtils;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpRequestDecorator;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.Optional;

/**
 * Reads a small request body once and replays it, so a {@link org.springframework.web.server.WebFilter}
 * can inspect a credential payload without stealing it from the controller.
 *
 * <p>Only used for the auth endpoints, where the per-email rate-limit key lives in the body.
 * The replay is a hard requirement: a filter that consumes the body turns every login into an
 * empty 400.</p>
 */
final class CachedRequestBody {
    /**
     * Credential payloads are tiny; anything larger is not a login request and is passed
     * through unread.
     */
    static final int MAX_BYTES = 16 * 1024;

    private CachedRequestBody() {
    }

    /**
     * @return the decoded body, or empty when it is absent or larger than {@link #MAX_BYTES}
     */
    static Mono<Optional<String>> read(ServerWebExchange exchange) {
        return DataBufferUtils.join(exchange.getRequest().getBody(), MAX_BYTES)
                .map(buffer -> {
                    byte[] bytes = new byte[buffer.readableByteCount()];
                    buffer.read(bytes);
                    DataBufferUtils.release(buffer);
                    return Optional.of(new String(bytes, StandardCharsets.UTF_8));
                })
                .defaultIfEmpty(Optional.empty())
                .onErrorResume(error -> Mono.just(Optional.empty()));
    }

    /**
     * Returns an exchange whose body yields the cached payload exactly once.
     */
    static ServerWebExchange replay(ServerWebExchange exchange, String body) {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ServerHttpRequest request = exchange.getRequest().mutate()
                .header(org.springframework.http.HttpHeaders.CONTENT_LENGTH, String.valueOf(bytes.length))
                .build();
        return exchange.mutate()
                .request(new ServerHttpRequestDecorator(request) {
                    @Override
                    public Flux<DataBuffer> getBody() {
                        return Flux.defer(() -> Flux.just(
                                exchange.getResponse().bufferFactory().wrap(bytes)
                        ));
                    }
                })
                .build();
    }
}
