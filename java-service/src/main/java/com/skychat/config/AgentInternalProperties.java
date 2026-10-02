package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Credentials the Java service uses when it calls the Python agent service.
 *
 * <p>{@code internalJwtSecret} is the HS256 key for the short-lived internal service JWT and
 * must be configured explicitly; {@code token} is the legacy static bearer and is only kept
 * as a fallback for environments that have not migrated yet.</p>
 */
@ConfigurationProperties(prefix = "agent-service")
public record AgentInternalProperties(
        String internalJwtSecret,
        String token
) {
}
