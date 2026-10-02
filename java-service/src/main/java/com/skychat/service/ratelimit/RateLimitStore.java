package com.skychat.service.ratelimit;

import reactor.core.publisher.Mono;

/**
 * A fixed-window counter backend.
 *
 * <p>Implementations must never signal an error: rate limiting protects availability, so a
 * broken backend has to degrade into "allow" rather than "deny everything".</p>
 */
public interface RateLimitStore {
    /**
     * Atomically increments {@code key} and starts a {@code windowSeconds} window on the first
     * increment.
     *
     * @return the count inside the current window and the seconds until it expires
     */
    Mono<Counter> increment(String key, long windowSeconds);

    /**
     * Reads the current window without consuming a hit. An expired window reads as zero.
     */
    Mono<Counter> peek(String key);

    /**
     * Clears a counter, e.g. after a successful login resets the failed-attempt window.
     */
    Mono<Void> reset(String key);

    record Counter(long count, long ttlSeconds) {
    }
}
