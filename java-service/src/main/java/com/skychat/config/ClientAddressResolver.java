package com.skychat.config;

import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;

/**
 * Resolves the client address for rate limiting and the audit trail.
 *
 * <p>{@code X-Forwarded-For} is attacker controlled unless a trusted reverse proxy always
 * rewrites it, so it is honoured only when {@code skychat.http.trust-forwarded-for} is true.
 * Otherwise the transport level remote address is used.</p>
 */
@Component
public class ClientAddressResolver {
    private static final int MAX_LENGTH = 64;

    /**
     * Used when there is no resolvable address. {@code null} is the honest answer: a shared
     * constant like {@code "unknown"} would put every address-less caller into one bucket and
     * make the limiter deny unrelated traffic.
     */
    private static final String UNRESOLVED = null;

    private final SecurityProperties properties;

    public ClientAddressResolver(SecurityProperties properties) {
        this.properties = properties;
    }

    /**
     * @return the client address, or {@code null} when it cannot be determined
     */
    public String resolve(ServerHttpRequest request) {
        if (request == null) {
            return UNRESOLVED;
        }

        if (properties != null && properties.http() != null && properties.http().trustForwardedFor()) {
            String forwarded = request.getHeaders().getFirst("X-Forwarded-For");
            if (forwarded != null && !forwarded.isBlank()) {
                // The left-most entry is the original client; everything after it is proxy hop
                // information that a client can forge just as easily.
                String first = forwarded.split(",")[0].trim();
                if (!first.isEmpty()) {
                    return truncate(first);
                }
            }
        }

        if (request.getRemoteAddress() == null
                || request.getRemoteAddress().getAddress() == null) {
            return UNRESOLVED;
        }
        return truncate(request.getRemoteAddress().getAddress().getHostAddress());
    }

    private String truncate(String value) {
        return value.length() <= MAX_LENGTH ? value : value.substring(0, MAX_LENGTH);
    }
}
