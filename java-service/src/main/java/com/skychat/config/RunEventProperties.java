package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Bounds for the in-process run-event buffer.
 *
 * <p>The buffer keeps the events of in-flight (and recently finished) runs so a client can
 * resume an SSE stream with {@code Last-Event-ID}. It used to grow without limit: every run
 * and every event stayed in the heap forever, so a long-lived process leaked steadily.</p>
 */
@ConfigurationProperties(prefix = "skychat.run-events")
public record RunEventProperties(
        @DefaultValue("500") int maxRuns,
        @DefaultValue("1800") int ttlSeconds,
        @DefaultValue("2000") int maxEventsPerRun,
        @DefaultValue("60000") long cleanupIntervalMs
) {
}
