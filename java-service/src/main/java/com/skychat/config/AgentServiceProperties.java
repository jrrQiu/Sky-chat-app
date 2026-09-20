package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "agent-service")
public record AgentServiceProperties(String url, String token) {
}
