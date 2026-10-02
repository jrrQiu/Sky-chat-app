/**
 * Rate limiting backends and the reusable fixed-window limiter.
 *
 * <p>Redis is the shared enforcement point; the in-process store is an explicit,
 * documented degradation for a Redis outage.</p>
 */
package com.skychat.service.ratelimit;
