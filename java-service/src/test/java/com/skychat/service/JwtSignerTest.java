package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The hand-rolled HS256 signer.
 *
 * <p>Deliberately asserts on the raw wire format as well as on the round trip: the Python agent
 * service verifies these tokens independently, so "our verifier accepts it" is not sufficient
 * evidence that the format is right.</p>
 */
class JwtSignerTest {

    private static final String SECRET = "unit-test-internal-secret";
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final JwtSigner signer = new JwtSigner(objectMapper);

    @Test
    @DisplayName("the token header is a base64url HS256 JWT header and the payload carries the claims")
    void wireFormatAndClaims() throws Exception {
        String token = signer.sign(
                SECRET,
                "sky-chat-java-service",
                "sky-chat-agent-service",
                "user-1",
                List.of("approver", "employee"),
                60
        );

        String[] parts = token.split("\\.", -1);
        assertThat(parts).hasSize(3);
        // base64url without padding: no '=' and no '+' or '/'.
        assertThat(token).doesNotContain("=");
        assertThat(parts[0]).doesNotContain("+").doesNotContain("/");

        String header = new String(URL_DECODER.decode(parts[0]), StandardCharsets.UTF_8);
        assertThat(header).isEqualTo("{\"alg\":\"HS256\",\"typ\":\"JWT\"}");

        var payload = objectMapper.readTree(URL_DECODER.decode(parts[1]));
        assertThat(payload.path("iss").asText()).isEqualTo("sky-chat-java-service");
        assertThat(payload.path("aud").asText()).isEqualTo("sky-chat-agent-service");
        assertThat(payload.path("sub").asText()).isEqualTo("user-1");
        assertThat(payload.path("roles").get(0).asText()).isEqualTo("approver");
        assertThat(payload.path("jti").asText()).isNotBlank();

        long iat = payload.path("iat").asLong();
        long exp = payload.path("exp").asLong();
        assertThat(exp - iat).isEqualTo(60);
        // exp ~60s ahead of now, allowing a second of clock skew.
        assertThat(exp).isBetween(Instant.now().getEpochSecond() + 59, Instant.now().getEpochSecond() + 61);
    }

    @Test
    @DisplayName("a signed token round-trips and reports its expiry")
    void roundTrip() {
        String token = signer.sign(
                SECRET,
                "sky-chat-java-service",
                "sky-chat-agent-service",
                "user-42",
                List.of("admin"),
                60
        );

        JwtSigner.Token claims = signer.verify(SECRET, token);

        assertThat(claims.subject()).isEqualTo("user-42");
        assertThat(claims.roles()).containsExactly("admin");
        assertThat(claims.issuer()).isEqualTo("sky-chat-java-service");
        assertThat(claims.audience()).isEqualTo("sky-chat-agent-service");
        assertThat(claims.expiresAt()).isEqualTo(claims.issuedAt() + 60);
    }

    @Test
    @DisplayName("a tampered payload is rejected")
    void tamperedPayloadIsRejected() throws Exception {
        String token = signer.sign(
                SECRET,
                "sky-chat-java-service",
                "sky-chat-agent-service",
                "user-1",
                List.of("employee"),
                60
        );
        String[] parts = token.split("\\.", -1);

        // Re-encode the payload with an escalated role, keeping the original signature.
        var payload = objectMapper.readTree(URL_DECODER.decode(parts[1]));
        ((com.fasterxml.jackson.databind.node.ObjectNode) payload)
                .putArray("roles")
                .add("admin");
        String forgedPayload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(objectMapper.writeValueAsBytes(payload));
        String forged = parts[0] + "." + forgedPayload + "." + parts[2];

        assertThatThrownBy(() -> signer.verify(SECRET, forged))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("signature");
    }

    @Test
    @DisplayName("a token signed with another secret is rejected")
    void wrongSecretIsRejected() {
        String token = signer.sign(
                "other-secret",
                "sky-chat-java-service",
                "sky-chat-agent-service",
                "user-1",
                List.of(),
                60
        );

        assertThatThrownBy(() -> signer.verify(SECRET, token))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("signature");
    }

    @Test
    @DisplayName("a malformed or empty token is rejected")
    void malformedIsRejected() {
        assertThatThrownBy(() -> signer.verify(SECRET, "not-a-token"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> signer.verify(SECRET, ""))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> signer.verify(SECRET, null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("an expired token is rejected even with a valid signature")
    void expiredIsRejected() {
        // A negative TTL produces exp in the past while still signing correctly.
        String token = signer.sign(
                SECRET,
                "sky-chat-java-service",
                "sky-chat-agent-service",
                "user-1",
                List.of(),
                -10
        );

        assertThatThrownBy(() -> signer.verify(SECRET, token))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Expired");
    }

    @Test
    @DisplayName("the user-token shape round-trips with its email claim")
    void userTokenRoundTrip() {
        String token = signer.signUserToken(
                "user-secret",
                "user-7",
                "user7@example.com",
                List.of("employee"),
                Instant.now().getEpochSecond() + 3600
        );

        JwtSigner.UserToken claims = signer.verifyUserToken("user-secret", token);

        assertThat(claims.userId()).isEqualTo("user-7");
        assertThat(claims.email()).isEqualTo("user7@example.com");
        assertThat(claims.roles()).containsExactly("employee");
    }
}
