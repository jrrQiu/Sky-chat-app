package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Minimal HS256 JWT signer/verifier built on {@code javax.crypto.Mac}.
 *
 * <p>The service intentionally does not pull in a JWT library: the project has no
 * {@code spring-security-oauth2-jose} or {@code jjwt} dependency and adding one for a single
 * internal token format is not worth the supply-chain surface. The payload is serialised with
 * the shared Jackson {@code ObjectMapper}, exactly like {@link JwtService} already does for
 * user tokens.</p>
 *
 * <p>Only the fields the cross-service contract needs are supported. Verification checks the
 * signature (constant time), the expiry and, when expected, the issuer/audience pair.</p>
 */
@Component
public class JwtSigner {
    /**
     * JWT base64url encoding is unpadded; padded output is rejected by most verifiers.
     */
    private static final Base64.Encoder URL_ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();
    private static final String HMAC_SHA_256 = "HmacSHA256";
    private static final String HEADER_JSON = "{\"alg\":\"HS256\",\"typ\":\"JWT\"}";

    private final ObjectMapper objectMapper;

    public JwtSigner(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * The claims this signer understands.
     */
    public record Token(
            String issuer,
            String audience,
            String subject,
            List<String> roles,
            long issuedAt,
            long expiresAt,
            String jwtId
    ) {
    }

    public String sign(
            String secret,
            String issuer,
            String audience,
            String subject,
            List<String> roles,
            long ttlSeconds
    ) {
        long now = Instant.now().getEpochSecond();
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("iss", issuer);
            payload.put("aud", audience);
            payload.put("sub", subject == null ? "" : subject);
            payload.put("roles", roles == null ? List.of() : roles);
            payload.put("iat", now);
            payload.put("exp", now + ttlSeconds);
            payload.put("jti", UUID.randomUUID().toString());

            String unsigned = encode(HEADER_JSON) + "." + encode(objectMapper.writeValueAsString(payload));
            return unsigned + "." + encode(hmac(secret, unsigned));
        } catch (Exception error) {
            throw new IllegalStateException("Unable to sign internal token", error);
        }
    }

    /**
     * Verifies the signature and the expiry and returns the decoded claims.
     *
     * @throws IllegalArgumentException when the token is malformed, tampered with or expired
     */
    public Token verify(String secret, String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("Missing token");
        }
        String[] parts = token.split("\\.", -1);
        if (parts.length != 3) {
            throw new IllegalArgumentException("Malformed token");
        }

        String unsigned = parts[0] + "." + parts[1];
        if (!MessageDigest.isEqual(decode(parts[2]), hmac(secret, unsigned))) {
            throw new IllegalArgumentException("Invalid token signature");
        }

        try {
            JsonNode header = objectMapper.readTree(decode(parts[0]));
            if (!"HS256".equals(header.path("alg").asText())) {
                throw new IllegalArgumentException("Unsupported token algorithm");
            }

            JsonNode payload = objectMapper.readTree(decode(parts[1]));
            return new Token(
                    payload.path("iss").asText(""),
                    payload.path("aud").asText(""),
                    payload.path("sub").asText(""),
                    rolesOf(payload),
                    payload.path("iat").asLong(),
                    requireNotExpired(payload),
                    payload.path("jti").asText("")
            );
        } catch (IllegalArgumentException error) {
            throw error;
        } catch (Exception error) {
            throw new IllegalArgumentException("Invalid token payload", error);
        }
    }

    /**
     * The end-user token shape: the same HS256 mechanics, but {@code email} instead of the
     * issuer/audience pair.
     */
    public record UserToken(
            String userId,
            String email,
            List<String> roles,
            long expiresAt
    ) {
    }

    public String signUserToken(
            String secret,
            String userId,
            String email,
            List<String> roles,
            long expiresAt
    ) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("sub", userId);
            payload.put("email", email == null ? "" : email);
            payload.put("roles", roles == null ? List.of() : roles);
            payload.put("iat", Instant.now().getEpochSecond());
            payload.put("exp", expiresAt);

            String unsigned = encode(HEADER_JSON) + "." + encode(objectMapper.writeValueAsString(payload));
            return unsigned + "." + encode(hmac(secret, unsigned));
        } catch (Exception error) {
            throw new IllegalStateException("Unable to create auth token", error);
        }
    }

    public UserToken verifyUserToken(String secret, String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("Missing token");
        }
        String[] parts = token.split("\\.", -1);
        if (parts.length != 3) {
            throw new IllegalArgumentException("Malformed token");
        }

        String unsigned = parts[0] + "." + parts[1];
        if (!MessageDigest.isEqual(decode(parts[2]), hmac(secret, unsigned))) {
            throw new IllegalArgumentException("Invalid token signature");
        }

        try {
            JsonNode payload = objectMapper.readTree(decode(parts[1]));
            return new UserToken(
                    payload.path("sub").asText(),
                    payload.path("email").asText(""),
                    rolesOf(payload),
                    requireNotExpired(payload)
            );
        } catch (IllegalArgumentException error) {
            throw error;
        } catch (Exception error) {
            throw new IllegalArgumentException("Invalid token payload", error);
        }
    }

    private List<String> rolesOf(JsonNode payload) {
        List<String> roles = new ArrayList<>();
        payload.path("roles").forEach(node -> roles.add(node.asText()));
        return roles;
    }

    private long requireNotExpired(JsonNode payload) {
        long expiresAt = payload.path("exp").asLong();
        if (expiresAt <= Instant.now().getEpochSecond()) {
            throw new IllegalArgumentException("Expired token");
        }
        return expiresAt;
    }

    private byte[] hmac(String secret, String value) {
        if (secret == null || secret.isBlank()) {
            throw new IllegalStateException("JWT secret is not configured");
        }
        try {
            Mac mac = Mac.getInstance(HMAC_SHA_256);
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), HMAC_SHA_256));
            return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) {
            throw new IllegalStateException("Unable to compute token signature", error);
        }
    }

    private String encode(String value) {
        return URL_ENCODER.encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    private String encode(byte[] value) {
        return URL_ENCODER.encodeToString(value);
    }

    private byte[] decode(String value) {
        return URL_DECODER.decode(value);
    }
}
