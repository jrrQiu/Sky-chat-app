package com.skychat.service;

import com.skychat.config.AgentInternalProperties;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Mints the short-lived internal service JWT that the Python agent service verifies.
 *
 * <p>Contract (frozen): HS256, {@code iss=sky-chat-java-service},
 * {@code aud=sky-chat-agent-service}, {@code sub=<acting user id>}, {@code roles=[...]},
 * {@code iat}, {@code exp=iat+60s}, {@code jti}. The static {@code AGENT_SERVICE_TOKEN} is no
 * longer the primary credential.</p>
 *
 * <p>The secret has no working default on purpose. A blank secret would let any deployment
 * silently fall back to a guessable key, so the context refuses to start instead - failing at
 * startup is far cheaper than discovering unauthenticated internal calls in production.</p>
 */
@Service
public class InternalTokenService {
    private static final Logger log = LoggerFactory.getLogger(InternalTokenService.class);

    public static final String ISSUER = "sky-chat-java-service";
    public static final String AUDIENCE = "sky-chat-agent-service";
    public static final long TTL_SECONDS = 60L;

    private final AgentInternalProperties properties;
    private final JwtSigner signer;

    public InternalTokenService(AgentInternalProperties properties, JwtSigner signer) {
        this.properties = properties;
        this.signer = signer;
    }

    /**
     * Fails the application context when {@code AGENT_INTERNAL_JWT_SECRET} is absent, rather
     * than letting every agent call fail at request time with an opaque 401.
     */
    @PostConstruct
    void validateSecret() {
        if (secret() == null) {
            throw new IllegalStateException(
                    "AGENT_INTERNAL_JWT_SECRET (agent-service.internal-jwt-secret) is not "
                            + "configured. The Java service refuses to start without a secret "
                            + "to sign internal agent-service tokens; a default would make "
                            + "every internal call forgeable."
            );
        }
        if (legacyTokenConfigured()) {
            log.warn(
                    "AGENT_SERVICE_TOKEN is configured but ignored: internal agent-service "
                            + "calls now carry a short-lived signed JWT. Remove the static "
                            + "token once the agent service verifies the JWT."
            );
        }
    }

    /**
     * A fresh token for every outgoing call: the 60s window is short enough that reuse across
     * calls would regularly cross the expiry boundary.
     */
    public String createToken(String userId, List<String> roles) {
        String secret = secret();
        if (secret == null) {
            throw new IllegalStateException("AGENT_INTERNAL_JWT_SECRET is not configured");
        }
        return signer.sign(secret, ISSUER, AUDIENCE, userId, roles, TTL_SECONDS);
    }

    public JwtSigner.Token verify(String token) {
        String secret = secret();
        if (secret == null) {
            throw new IllegalStateException("AGENT_INTERNAL_JWT_SECRET is not configured");
        }
        JwtSigner.Token claims = signer.verify(secret, token);
        if (!ISSUER.equals(claims.issuer()) || !AUDIENCE.equals(claims.audience())) {
            throw new IllegalArgumentException("Token is not an internal agent-service token");
        }
        return claims;
    }

    public boolean isConfigured() {
        return secret() != null;
    }

    /**
     * The legacy static token, used only when no signing secret exists at all. Callers must
     * have logged the warning from {@link #validateSecret()}.
     */
    public String legacyFallbackToken() {
        String token = properties.token();
        return token == null || token.isBlank() ? null : token;
    }

    private boolean legacyTokenConfigured() {
        return legacyFallbackToken() != null;
    }

    private String secret() {
        String secret = properties.internalJwtSecret();
        return secret == null || secret.isBlank() ? null : secret;
    }
}
