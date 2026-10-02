package com.skychat.service.ratelimit;

import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Fallback fixed-window counter that lives in this JVM.
 *
 * <p><strong>This fallback is degraded by construction:</strong> the counter is per replica, so
 * N replicas behind a load balancer allow up to N times the configured limit, and every restart
 * forgets all windows. It only exists so that a Redis outage degrades the limit instead of
 * taking the service down (fail-open on availability). The shared
 * {@link RedisRateLimitStore} is the real enforcement point.</p>
 */
@Component
public class InMemoryRateLimitStore implements RateLimitStore {
    private static final int SWEEP_THRESHOLD = 10_000;

    private final Map<String, Window> windows = new ConcurrentHashMap<>();

    @Override
    public Mono<Counter> increment(String key, long windowSeconds) {
        long now = System.currentTimeMillis();
        long windowMillis = Duration.ofSeconds(windowSeconds).toMillis();

        Window window = windows.compute(key, (ignored, current) -> {
            if (current == null || current.expiresAtMillis() <= now) {
                return new Window(1L, now + windowMillis);
            }
            return new Window(current.count() + 1, current.expiresAtMillis());
        });

        if (windows.size() > SWEEP_THRESHOLD) {
            sweep(now);
        }
        return Mono.just(new Counter(window.count(), remainingSeconds(window.expiresAtMillis(), now)));
    }

    @Override
    public Mono<Counter> peek(String key) {
        long now = System.currentTimeMillis();
        Window window = windows.get(key);
        if (window == null || window.expiresAtMillis() <= now) {
            return Mono.just(new Counter(0, 0));
        }
        return Mono.just(new Counter(window.count(), remainingSeconds(window.expiresAtMillis(), now)));
    }

    /**
     * Rounded up: with {@code retryAfterSeconds = 0} a client could retry immediately and be
     * refused again, so the remaining window is never under-reported.
     */
    private long remainingSeconds(long expiresAtMillis, long now) {
        long remainingMillis = expiresAtMillis - now;
        return remainingMillis <= 0 ? 0 : (remainingMillis + 999) / 1000;
    }

    @Override
    public Mono<Void> reset(String key) {
        windows.remove(key);
        return Mono.empty();
    }

    /**
     * Without this an attacker cycling keys could grow the map without bound.
     */
    private void sweep(long now) {
        windows.entrySet().removeIf(entry -> entry.getValue().expiresAtMillis() <= now);
    }

    private record Window(long count, long expiresAtMillis) {
    }
}
