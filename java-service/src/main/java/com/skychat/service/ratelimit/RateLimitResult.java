package com.skychat.service.ratelimit;

/**
 * Whether a single rate-limit check passed.
 */
public record RateLimitResult(boolean allowed, long retryAfterSeconds) {
    /**
     * Named {@code permit} rather than {@code allowed} because a record already generates the
     * {@code allowed()} accessor.
     */
    public static RateLimitResult permit() {
        return new RateLimitResult(true, 0);
    }

    public static RateLimitResult limited(long retryAfterSeconds) {
        return new RateLimitResult(false, Math.max(1, retryAfterSeconds));
    }
}
