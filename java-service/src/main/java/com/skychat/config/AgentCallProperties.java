package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * How the Java service behaves when the Python agent service is slow or unreachable.
 *
 * <p>Two timeouts, because they fail differently. {@code connectTimeoutMs} bounds the TCP
 * handshake and, when it trips, we know the request was never delivered — which is what makes
 * a retry safe. {@code idleTimeoutMs} bounds the gap <em>between</em> network reads on an
 * established connection: the chat endpoint streams server-sent events, so a total-duration
 * cap would truncate legitimate long generations. It is an idle guard, not a deadline.</p>
 */
@ConfigurationProperties(prefix = "skychat.agent-call")
public record AgentCallProperties(
        @DefaultValue("3000") int connectTimeoutMs,
        @DefaultValue("120000") int idleTimeoutMs,
        @DefaultValue("2") int retryAttempts,
        @DefaultValue("300") long retryBackoffMs,
        @DefaultValue("5") int breakerFailureThreshold,
        @DefaultValue("30000") long breakerOpenMs
) {
}
