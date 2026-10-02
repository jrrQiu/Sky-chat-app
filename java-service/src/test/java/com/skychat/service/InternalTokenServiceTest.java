package com.skychat.service;

import com.skychat.config.AgentInternalProperties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The internal service JWT the Python agent service verifies.
 *
 * <p>Uses the real {@link JwtSigner} so the test covers the exact bytes that go on the wire,
 * rather than a stub's idea of them.</p>
 */
class InternalTokenServiceTest {

    private static final String SECRET = "internal-jwt-secret-for-tests";

    private final JwtSigner signer = new JwtSigner(new com.fasterxml.jackson.databind.ObjectMapper());

    private InternalTokenService service(String secret, String legacyToken) {
        return new InternalTokenService(
                new AgentInternalProperties(secret, legacyToken),
                signer
        );
    }

    @Test
    @DisplayName("the minted token carries the frozen header and claim set")
    void claimsMatchTheFrozenContract() throws Exception {
        InternalTokenService service = service(SECRET, null);

        String token = service.createToken("user-9", java.util.List.of("approver"));
        String[] parts = token.split("\\.", -1);

        assertThat(parts).hasSize(3);
        String header = new String(
                java.util.Base64.getUrlDecoder().decode(parts[0]),
                java.nio.charset.StandardCharsets.UTF_8
        );
        assertThat(header).isEqualTo("{\"alg\":\"HS256\",\"typ\":\"JWT\"}");

        JwtSigner.Token claims = service.verify(token);
        assertThat(claims.issuer()).isEqualTo("sky-chat-java-service");
        assertThat(claims.audience()).isEqualTo("sky-chat-agent-service");
        assertThat(claims.subject()).isEqualTo("user-9");
        assertThat(claims.roles()).containsExactly("approver");
        assertThat(claims.expiresAt() - claims.issuedAt()).isEqualTo(60);
        assertThat(claims.jwtId()).isNotBlank();
    }

    @Test
    @DisplayName("the expiry is about 60 seconds ahead")
    void expiryIsSixtySecondsAhead() {
        InternalTokenService service = service(SECRET, null);

        JwtSigner.Token claims = service.verify(service.createToken("user-1", java.util.List.of()));
        long now = java.time.Instant.now().getEpochSecond();

        assertThat(claims.expiresAt()).isBetween(now + 59, now + 61);
    }

    @Test
    @DisplayName("each call mints a distinct token (fresh jti, never reused)")
    void tokensAreUnique() {
        InternalTokenService service = service(SECRET, null);

        String first = service.createToken("user-1", java.util.List.of());
        String second = service.createToken("user-1", java.util.List.of());

        assertThat(first).isNotEqualTo(second);
    }

    @Test
    @DisplayName("a token with a swapped subject is rejected")
    void tamperingIsRejected() throws Exception {
        InternalTokenService service = service(SECRET, null);
        String token = service.createToken("user-1", java.util.List.of("employee"));
        String[] parts = token.split("\\.", -1);

        var mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        var payload = mapper.readTree(java.util.Base64.getUrlDecoder().decode(parts[1]));
        ((com.fasterxml.jackson.databind.node.ObjectNode) payload).put("sub", "user-admin");
        String forgedPayload = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString(mapper.writeValueAsBytes(payload));

        assertThatThrownBy(() -> service.verify(parts[0] + "." + forgedPayload + "." + parts[2]))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("an unsigned 'none' algorithm token is rejected")
    void noneAlgorithmIsRejected() {
        InternalTokenService service = service(SECRET, null);
        String header = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("{\"alg\":\"none\",\"typ\":\"JWT\"}"
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8));
        String payload = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("{\"iss\":\"sky-chat-java-service\"}"
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8));

        assertThatThrownBy(() -> service.verify(header + "." + payload + "."))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("a token for a different audience cannot be replayed as an internal token")
    void foreignAudienceIsRejected() {
        InternalTokenService service = service(SECRET, null);
        String foreign = signer.sign(
                SECRET,
                "some-other-service",
                "some-other-audience",
                "user-1",
                java.util.List.of(),
                60
        );

        assertThatThrownBy(() -> service.verify(foreign))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("internal agent-service token");
    }

    @Test
    @DisplayName("a blank secret fails the context at startup instead of shipping a guessable key")
    void blankSecretFailsFast() {
        assertThatThrownBy(() -> service("", null).validateSecret())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("AGENT_INTERNAL_JWT_SECRET");

        assertThatThrownBy(() -> service(null, null).validateSecret())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("AGENT_INTERNAL_JWT_SECRET");
    }

    @Test
    @DisplayName("the legacy static token still round-trips for compatibility")
    void legacyTokenRemainsAvailable() {
        InternalTokenService service = service(SECRET, "legacy-static-token");

        assertThat(service.legacyFallbackToken()).isEqualTo("legacy-static-token");
        assertThat(service.isConfigured()).isTrue();
        // It is no longer a credential: the new token is a signed JWT, not the static string.
        assertThat(service.createToken("user-1", java.util.List.of()))
                .isNotEqualTo("legacy-static-token");
    }

    @Test
    @DisplayName("without a signing secret there is no token to mint")
    void noTokenWithoutSecret() {
        InternalTokenService service = service("  ", null);

        assertThat(service.isConfigured()).isFalse();
        assertThatThrownBy(() -> service.createToken("user-1", java.util.List.of()))
                .isInstanceOf(IllegalStateException.class);
    }
}
