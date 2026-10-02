package com.skychat.service;

import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

/**
 * Invitation token issuing and verification.
 *
 * <p>The raw token is shown once, inside the invite URL, and never stored: only its
 * SHA-256 is persisted. A plain hash (no salt, no stretching) is the right choice
 * here — unlike a password, the token is 256 bits of CSPRNG output, so there is
 * nothing to brute-force and the lookup has to stay a single indexed equality.</p>
 */
@Component
public class InvitationTokens {
    private static final int TOKEN_BYTES = 32;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();

    public String issue() {
        byte[] bytes = new byte[TOKEN_BYTES];
        RANDOM.nextBytes(bytes);
        return ENCODER.encodeToString(bytes);
    }

    public String hash(String rawToken) {
        if (rawToken == null || rawToken.isBlank()) {
            return "";
        }
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(
                    digest.digest(rawToken.trim().getBytes(StandardCharsets.UTF_8))
            );
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is not available", error);
        }
    }

    /**
     * Constant-time comparison, used by tests and by any place that compares a token
     * rather than looking it up by hash.
     */
    public boolean matches(String rawToken, String storedHash) {
        String candidate = hash(rawToken);
        if (candidate.isEmpty() || storedHash == null) {
            return false;
        }
        return MessageDigest.isEqual(
                candidate.getBytes(StandardCharsets.UTF_8),
                storedHash.getBytes(StandardCharsets.UTF_8)
        );
    }
}
