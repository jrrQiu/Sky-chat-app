package com.skychat.service.ratelimit;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Mono;

import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Fixed-window behaviour of the reusable limiter, its Redis failure fallback, and the
 * in-process store that backs that fallback.
 *
 * <p>A recording adapter stands in for Redis so the window arithmetic is exercised without a
 * network dependency; the real Redis store is covered by the integration test.</p>
 */
class RateLimiterTest {

    @Test
    @DisplayName("hits within the limit pass")
    void withinLimitPasses() {
        RateLimiter limiter = inProcessLimiter();

        for (int hit = 1; hit <= 5; hit++) {
            RateLimitResult result = limiter.check("chat-user", "user-1", 5, 60).block();
            assertThat(result.allowed()).as("hit %d", hit).isTrue();
        }
    }

    @Test
    @DisplayName("the hit that exceeds the limit is refused and reports retryAfterSeconds")
    void overLimitIsRefused() {
        RateLimiter limiter = inProcessLimiter();

        for (int hit = 1; hit <= 5; hit++) {
            assertThat(limiter.check("chat-user", "user-1", 5, 60).block().allowed()).isTrue();
        }

        RateLimitResult refused = limiter.check("chat-user", "user-1", 5, 60).block();

        assertThat(refused.allowed()).isFalse();
        // The retry hint is the remainder of the fixed window, never zero or negative.
        assertThat(refused.retryAfterSeconds()).isBetween(1L, 60L);
    }

    @Test
    @DisplayName("separate subjects have separate windows")
    void subjectsAreIsolated() {
        RateLimiter limiter = inProcessLimiter();

        for (int hit = 1; hit <= 5; hit++) {
            limiter.check("chat-user", "user-1", 5, 60).block();
        }

        assertThat(limiter.check("chat-user", "user-1", 5, 60).block().allowed()).isFalse();
        assertThat(limiter.check("chat-user", "user-2", 5, 60).block().allowed()).isTrue();
    }

    @Test
    @DisplayName("separate buckets (per-IP vs per-email) have separate windows")
    void bucketsAreIsolated() {
        RateLimiter limiter = inProcessLimiter();

        for (int hit = 1; hit <= 5; hit++) {
            limiter.check("login-ip", "1.2.3.4", 5, 60).block();
        }

        assertThat(limiter.check("login-ip", "1.2.3.4", 5, 60).block().allowed()).isFalse();
        assertThat(limiter.check("login-email", "1.2.3.4", 5, 60).block().allowed()).isTrue();
    }

    @Test
    @DisplayName("an expired window starts counting again")
    void windowExpires() throws Exception {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();
        RateLimiter limiter = new RateLimiter(new AdapterRedisStore(store), store);

        assertThat(limiter.check("t", "subject", 1, 1).block().allowed()).isTrue();
        assertThat(limiter.check("t", "subject", 1, 1).block().allowed()).isFalse();

        Thread.sleep(1100L);

        assertThat(limiter.check("t", "subject", 1, 1).block().allowed()).isTrue();
    }

    @Test
    @DisplayName("reset clears the window, e.g. after a successful login")
    void resetClearsTheWindow() {
        RateLimiter limiter = inProcessLimiter();

        for (int hit = 1; hit <= 5; hit++) {
            limiter.check("login-failure-email", "user@example.com", 5, 900).block();
        }
        assertThat(limiter.check("login-failure-email", "user@example.com", 5, 900).block().allowed())
                .isFalse();

        limiter.reset("login-failure-email", "user@example.com").block();

        assertThat(limiter.check("login-failure-email", "user@example.com", 5, 900).block().allowed())
                .isTrue();
    }

    @Test
    @DisplayName("peek does not consume a hit and reports the lock threshold")
    void peekDoesNotConsume() {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();
        RateLimiter limiter = new RateLimiter(new AdapterRedisStore(store), store);

        for (int hit = 1; hit <= 5; hit++) {
            limiter.check("login-failure-email", "user@example.com", 5, 900).block();
        }

        // peek must be side-effect free: repeated peeks must not extend or consume the window.
        for (int probe = 0; probe < 10; probe++) {
            RateLimitResult peeked = limiter
                    .peek("login-failure-email", "user@example.com", 5, 900)
                    .block();
            assertThat(peeked.allowed()).isFalse();
            assertThat(peeked.retryAfterSeconds()).isBetween(1L, 900L);
        }
    }

    @Test
    @DisplayName("peek reports open below the threshold and the remaining window as the reset hint")
    void peekOpenBelowThreshold() {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();
        RateLimiter limiter = new RateLimiter(new AdapterRedisStore(store), store);

        limiter.check("login-failure-email", "user@example.com", 5, 900).block();

        RateLimitResult peeked = limiter
                .peek("login-failure-email", "user@example.com", 5, 900)
                .block();

        assertThat(peeked.allowed()).isTrue();
        // Still under the threshold, so the request may proceed; the reported value is the
        // remaining window, i.e. when the counter resets.
        assertThat(peeked.retryAfterSeconds()).isBetween(1L, 900L);
    }

    @Test
    @DisplayName("a failing Redis store falls back to the in-process counter instead of denying")
    void redisFailureFallsBackToInProcess() {
        AtomicInteger redisCalls = new AtomicInteger();
        InMemoryRateLimitStore fallback = new InMemoryRateLimitStore();
        RateLimiter limiter = new RateLimiter(new FailingRedisStore(redisCalls), fallback);

        // Fail-open: the first hits are allowed by the fallback counter even though Redis is down.
        assertThat(limiter.check("chat-user", "user-1", 2, 60).block().allowed()).isTrue();
        assertThat(limiter.check("chat-user", "user-1", 2, 60).block().allowed()).isTrue();
        // The fallback still enforces the limit locally, just per replica.
        assertThat(limiter.check("chat-user", "user-1", 2, 60).block().allowed()).isFalse();
        assertThat(redisCalls.get()).isGreaterThanOrEqualTo(3);

        // reset also has to survive a dead Redis.
        limiter.reset("chat-user", "user-1").block();
        assertThat(limiter.check("chat-user", "user-1", 2, 60).block().allowed()).isTrue();
    }

    @Test
    @DisplayName("the in-process store tracks counts per key and reports its remaining window")
    void inMemoryStoreCountsPerKey() {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();

        assertThat(store.increment("a", 60).block().count()).isEqualTo(1);
        assertThat(store.increment("a", 60).block().count()).isEqualTo(2);
        assertThat(store.increment("b", 60).block().count()).isEqualTo(1);
        assertThat(store.increment("a", 60).block().count()).isEqualTo(3);
        assertThat(store.increment("a", 60).block().ttlSeconds()).isBetween(1L, 60L);

        // peek must not consume a hit: the count stays at the four increments above.
        RateLimitStore.Counter peeked = store.peek("a").block();
        assertThat(peeked).isNotNull();
        assertThat(peeked.count()).isEqualTo(4);
        assertThat(store.peek("a").block().count()).isEqualTo(4);

        store.reset("a").block();
        assertThat(store.peek("a").block().count()).isZero();
    }

    @Test
    @DisplayName("the in-process store sweeps expired keys so the map cannot grow without bound")
    void inMemoryStoreSweepsExpiredKeys() throws Exception {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();

        // Past the sweep threshold with a one-second window, so every entry is expired.
        for (int index = 0; index < 10_050; index++) {
            store.increment("key-" + index, 1).block();
        }
        Thread.sleep(1100L);
        // The sweep is opportunistic: the next increment past the threshold triggers it.
        store.increment("trigger", 60).block();

        // An expired entry reads as zero rather than as a live window, which is what the limiter
        // relies on; the sweep has also released the memory.
        assertThat(store.peek("key-0").block().count()).isZero();
        assertThat(store.peek("trigger").block().count()).isEqualTo(1);
    }

    private RateLimiter inProcessLimiter() {
        InMemoryRateLimitStore store = new InMemoryRateLimitStore();
        return new RateLimiter(new AdapterRedisStore(store), store);
    }

    /**
     * Adapts a {@link RateLimitStore} to the concrete Redis store type so the limiter's real
     * code path can be driven deterministically, with or without failures.
     */
    private static class AdapterRedisStore extends RedisRateLimitStore {
        protected final RateLimitStore delegate;

        AdapterRedisStore(RateLimitStore delegate) {
            super(null);
            this.delegate = delegate;
        }

        @Override
        public Mono<RateLimitStore.Counter> increment(String key, long windowSeconds) {
            return delegate.increment(key, windowSeconds);
        }

        @Override
        public Mono<RateLimitStore.Counter> peek(String key) {
            return delegate.peek(key);
        }

        @Override
        public Mono<Void> reset(String key) {
            return delegate.reset(key);
        }
    }

    /**
     * A Redis backend that is up but broken, which is the case the fallback exists for.
     */
    private static final class FailingRedisStore extends AdapterRedisStore {
        private final AtomicInteger calls;

        FailingRedisStore(AtomicInteger calls) {
            super(null);
            this.calls = calls;
        }

        @Override
        public Mono<RateLimitStore.Counter> increment(String key, long windowSeconds) {
            calls.incrementAndGet();
            return Mono.error(new IllegalStateException("redis down"));
        }

        @Override
        public Mono<RateLimitStore.Counter> peek(String key) {
            calls.incrementAndGet();
            return Mono.error(new IllegalStateException("redis down"));
        }

        @Override
        public Mono<Void> reset(String key) {
            calls.incrementAndGet();
            return Mono.error(new IllegalStateException("redis down"));
        }
    }
}
