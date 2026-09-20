package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "auth")
public record AuthProperties(
        String jwtSecret,
        long jwtTtlSeconds
) {
}
