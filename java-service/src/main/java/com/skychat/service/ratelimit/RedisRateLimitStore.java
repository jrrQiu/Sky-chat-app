package com.skychat.service.ratelimit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.time.Duration;

/**
 * Redis fixed-window counter ({@code INCR} + {@code EXPIRE}) shared by every replica.
 *
 * <p>Only the reactive starter is on the classpath, so this uses
 * {@link ReactiveStringRedisTemplate}; there is no blocking {@code StringRedisTemplate} bean.</p>
 */
@Component
public class RedisRateLimitStore {
    private static final Logger log = LoggerFactory.getLogger(RedisRateLimitStore.class);

    private final ReactiveStringRedisTemplate redisTemplate;

    /**
     * One warning per process is enough: a Redis outage would otherwise log on every request.
     */
    private volatile boolean warned;

    public RedisRateLimitStore(ReactiveStringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    public Mono<RateLimitStore.Counter> increment(String key, long windowSeconds) {
        // Mono.defer keeps a missing template (or a failing client call) inside the reactive
        // chain, where RateLimiter's onErrorResume can fall back to the in-process counter.
        // Building the pipeline eagerly would throw straight out of this method instead.
        return Mono.defer(() -> redisTemplate.opsForValue()
                .increment(key)
                .flatMap(count -> {
                    if (count != null && count == 1L) {
                        return redisTemplate.expire(key, Duration.ofSeconds(windowSeconds))
                                .thenReturn(count);
                    }
                    return Mono.just(count == null ? 0L : count);
                })
                .flatMap(count -> ttlMillis(key).map(ttl -> new RateLimitStore.Counter(count, ttl))));
    }

    public Mono<Void> reset(String key) {
        return Mono.defer(() -> redisTemplate.delete(key).then());
    }

    public Mono<RateLimitStore.Counter> peek(String key) {
        return Mono.defer(() -> redisTemplate.opsForValue()
                .get(key)
                .defaultIfEmpty("0")
                .flatMap(raw -> {
                    long count = parse(raw);
                    return ttlMillis(key).map(ttl -> new RateLimitStore.Counter(
                            ttl <= 0 ? 0 : count,
                            ttl
                    ));
                }));
    }

    private long parse(String raw) {
        try {
            return Long.parseLong(raw);
        } catch (NumberFormatException error) {
            return 0;
        }
    }

    private Mono<Long> ttlMillis(String key) {
        return Mono.defer(() -> redisTemplate.getExpire(key)
                .map(duration -> {
                    long seconds = duration == null ? 0L : duration.getSeconds();
                    return Math.max(seconds, 0L);
                })
                .defaultIfEmpty(0L));
    }

    /**
     * Marks the outage once so callers can fall back to the in-process store loudly but not
     * noisily.
     */
    public void warnUnavailable(String key, Throwable error) {
        if (!warned) {
            warned = true;
            log.warn(
                    "Redis rate-limit backend unavailable (key={}); falling back to the "
                            + "per-replica in-process counter, which is degraded: limits are "
                            + "enforced independently on each replica and reset on restart. "
                            + "Cause: {}",
                    key,
                    error.toString()
            );
        }
    }
}
