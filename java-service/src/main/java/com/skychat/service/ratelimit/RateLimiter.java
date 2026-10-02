package com.skychat.service.ratelimit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Reusable fixed-window rate limiter used by the HTTP filter and by the login lockout.
 *
 * <p>Redis is the real enforcement point so the limit is shared across replicas. When Redis is
 * unreachable the limiter falls back to an in-process counter: <strong>that fallback is
 * degraded</strong> - it is per replica (N replicas allow N times the limit), it forgets every
 * window on restart, and it cannot see traffic that landed on another replica. It fails open on
 * purpose: a cache outage should not become a full outage of every guarded endpoint.</p>
 */
@Service
public class RateLimiter {
    private static final Logger log = LoggerFactory.getLogger(RateLimiter.class);

    private final RedisRateLimitStore redisStore;
    private final InMemoryRateLimitStore inMemoryStore;

    private final AtomicBoolean warnedAboutFallback = new AtomicBoolean(false);

    public RateLimiter(
            RedisRateLimitStore redisStore,
            InMemoryRateLimitStore inMemoryStore
    ) {
        this.redisStore = redisStore;
        this.inMemoryStore = inMemoryStore;
    }

    /**
     * Counts one hit against {@code bucket} and reports whether the caller is still inside
     * {@code limit} hits per {@code windowSeconds}.
     */
    public Mono<RateLimitResult> check(
            String bucket,
            String subject,
            int limit,
            long windowSeconds
    ) {
        String key = "skychat:rl:" + bucket + ":" + subject;
        return increment(key, windowSeconds)
                .map(counter -> counter.count() > limit
                        ? RateLimitResult.limited(counter.ttlSeconds())
                        : RateLimitResult.permit());
    }

    /**
     * Reads the current window without consuming a hit. Used by the login lockout, which has
     * to distinguish "locked" from "one more attempt allowed" before spending a password hash.
     */
    public Mono<RateLimitResult> peek(
            String bucket,
            String subject,
            int limit,
            long windowSeconds
    ) {
        String key = "skychat:rl:" + bucket + ":" + subject;
        return peekCounter(key, windowSeconds)
                .map(counter -> counter.count() >= limit
                        ? RateLimitResult.limited(counter.ttlSeconds())
                        : new RateLimitResult(true, Math.max(counter.ttlSeconds(), 0)));
    }

    /**
     * Resets a bucket, e.g. after a successful login clears the failed-attempt window.
     */
    public Mono<Void> reset(String bucket, String subject) {
        String key = "skychat:rl:" + bucket + ":" + subject;
        return redisStore.reset(key)
                .onErrorResume(error -> {
                    warnFallback(key, error);
                    return Mono.empty();
                })
                .then(inMemoryStore.reset(key));
    }

    private Mono<RateLimitStore.Counter> increment(String key, long windowSeconds) {
        return redisStore.increment(key, windowSeconds)
                .onErrorResume(error -> {
                    warnFallback(key, error);
                    return inMemoryStore.increment(key, windowSeconds);
                });
    }

    private Mono<RateLimitStore.Counter> peekCounter(String key, long windowSeconds) {
        return redisStore.peek(key)
                .onErrorResume(error -> {
                    warnFallback(key, error);
                    return inMemoryStore.peek(key);
                })
                .switchIfEmpty(inMemoryStore.peek(key))
                .defaultIfEmpty(new RateLimitStore.Counter(0, windowSeconds));
    }

    private void warnFallback(String key, Throwable error) {
        if (warnedAboutFallback.compareAndSet(false, true)) {
            log.warn(
                    "Redis rate-limit backend unavailable (key={}); using the per-replica "
                            + "in-process counter. Limits are degraded: they are enforced per "
                            + "replica, reset on restart and fail open. Cause: {}",
                    key,
                    error.toString()
            );
        }
    }
}
