package com.skychat.service.ratelimit;

/**
 * Compatibility shim left over from the first cut of the limiter store.
 *
 * <p>The real in-process implementation is {@link InMemoryRateLimitStore}. This type exists
 * only because the workspace sandbox refused its deletion; it is not referenced anywhere and
 * can be removed with a normal {@code git rm}.</p>
 *
 * @deprecated superseded by {@link InMemoryRateLimitStore}
 */
@Deprecated(since = "0.1.0", forRemoval = true)
public interface InProcessRateLimitStore extends RateLimitStore {
}
