package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.config.AuthProperties;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;

@Service
public class JwtService {
    public record Claims(String userId, String email, long expiresAt) {
    }

    private static final String HMAC_SHA_256 = "HmacSHA256";
    private static final Base64.Encoder URL_ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();

    private final AuthProperties properties;
    private final ObjectMapper objectMapper;

    public JwtService(AuthProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    public String createToken(String userId, String email) {
        long now = Instant.now().getEpochSecond();
        long expiresAt = now + properties.jwtTtlSeconds();

        try {
            String header = encodeUrl("{\"alg\":\"HS256\",\"typ\":\"JWT\"}");
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("sub", userId);
            payload.put("email", email);
            payload.put("iat", now);
            payload.put("exp", expiresAt);
            String body = encodeUrl(objectMapper.writeValueAsString(payload));
            String unsignedToken = header + "." + body;
            String signature = encodeUrl(sign(unsignedToken));
            return unsignedToken + "." + signature;
        } catch (Exception error) {
            throw new IllegalStateException("Unable to create auth token", error);
        }
    }

    public Claims verify(String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("Missing token");
        }

        String[] parts = token.split("\\.", -1);
        if (parts.length != 3) {
            throw new IllegalArgumentException("Malformed token");
        }

        String unsignedToken = parts[0] + "." + parts[1];
        if (!constantTimeEquals(decodeUrl(parts[2]), sign(unsignedToken))) {
            throw new IllegalArgumentException("Invalid token signature");
        }

        try {
            JsonNode payload = objectMapper.readTree(decodeUrl(parts[1]));
            long expiresAt = payload.path("exp").asLong();
            if (expiresAt <= Instant.now().getEpochSecond()) {
                throw new IllegalArgumentException("Expired token");
            }

            return new Claims(
                    payload.path("sub").asText(),
                    payload.path("email").asText(""),
                    expiresAt
            );
        } catch (IllegalArgumentException error) {
            throw error;
        } catch (Exception error) {
            throw new IllegalArgumentException("Invalid token payload", error);
        }
    }

    private byte[] sign(String value) {
        try {
            Mac mac = Mac.getInstance(HMAC_SHA_256);
            mac.init(new SecretKeySpec(
                    properties.jwtSecret().getBytes(StandardCharsets.UTF_8),
                    HMAC_SHA_256
            ));
            return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) {
            throw new IllegalStateException("Unable to sign token", error);
        }
    }

    private String encodeUrl(String value) {
        return URL_ENCODER.encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    private String encodeUrl(byte[] value) {
        return URL_ENCODER.encodeToString(value);
    }

    private byte[] decodeUrl(String value) {
        return URL_DECODER.decode(value);
    }

    private boolean constantTimeEquals(byte[] left, byte[] right) {
        return java.security.MessageDigest.isEqual(left, right);
    }
}
