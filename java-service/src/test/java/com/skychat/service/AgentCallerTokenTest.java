package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.config.AgentInternalProperties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The reverse internal contract: the agent service calling back into Java.
 *
 * <p>The security property worth pinning is that the two directions cannot be replayed against
 * each other. A single issuer/audience pair would mean an outbound token (or anything that
 * observed one) could be presented back to Java and treated as authority to act as the user it
 * named — the confused-deputy hole. Both claims are checked, so it cannot.</p>
 */
class AgentCallerTokenTest {

    private static final String SECRET = "internal-jwt-secret-for-tests";
    private static final String OTHER_SECRET = "a-different-secret-entirely";

    private final JwtSigner signer = new JwtSigner(new ObjectMapper());

    private InternalTokenService service(String secret) {
        return new InternalTokenService(new AgentInternalProperties(secret, null), signer);
    }

    private String mintCaller(String secret, String userId, List<String> roles) {
        return signer.sign(
                secret,
                InternalTokenService.CALLER_ISSUER,
                InternalTokenService.CALLER_AUDIENCE,
                userId,
                roles,
                60
        );
    }

    @Test
    @DisplayName("a correctly addressed caller token is accepted and yields the signed subject")
    void acceptsCorrectlyAddressedToken() {
        InternalTokenService service = service(SECRET);

        InternalTokenService.AgentCaller caller = service.verifyAgentCaller(
                mintCaller(SECRET, "user-1", List.of("it_staff", "employee"))
        );

        // The subject is authoritative: the agent service cannot claim to act as somebody else
        // once the token is signed.
        assertThat(caller.userId()).isEqualTo("user-1");
        assertThat(caller.roles()).containsExactly("it_staff", "employee");
    }

    /**
     * The confused-deputy guard. {@code createToken} produces the Java→Python pair; presenting
     * it back to Java must fail even though the signature is perfectly valid.
     */
    @Test
    @DisplayName("a token Java minted for the agent service is rejected when replayed at Java")
    void rejectsReplayedOutboundToken() {
        InternalTokenService service = service(SECRET);
        String outbound = service.createToken("victim-user", List.of("admin"));

        assertThatThrownBy(() -> service.verifyAgentCaller(outbound))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("not addressed to this service");
    }

    @Test
    @DisplayName("a caller token with the right audience but wrong issuer is rejected")
    void rejectsWrongIssuer() {
        InternalTokenService service = service(SECRET);
        String token = signer.sign(
                SECRET,
                "some-other-service",
                InternalTokenService.CALLER_AUDIENCE,
                "user-1",
                List.of("employee"),
                60
        );

        assertThatThrownBy(() -> service.verifyAgentCaller(token))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("a caller token with the right issuer but wrong audience is rejected")
    void rejectsWrongAudience() {
        InternalTokenService service = service(SECRET);
        String token = signer.sign(
                SECRET,
                InternalTokenService.CALLER_ISSUER,
                "sky-chat-something-else",
                "user-1",
                List.of("employee"),
                60
        );

        assertThatThrownBy(() -> service.verifyAgentCaller(token))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("a token signed with another secret is rejected")
    void rejectsForeignSignature() {
        InternalTokenService service = service(SECRET);

        assertThatThrownBy(() -> service.verifyAgentCaller(
                mintCaller(OTHER_SECRET, "user-1", List.of("employee"))
        ))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("signature");
    }

    @Test
    @DisplayName("an expired caller token is rejected")
    void rejectsExpiredToken() {
        InternalTokenService service = service(SECRET);
        String expired = signer.sign(
                SECRET,
                InternalTokenService.CALLER_ISSUER,
                InternalTokenService.CALLER_AUDIENCE,
                "user-1",
                List.of("employee"),
                -5
        );

        assertThatThrownBy(() -> service.verifyAgentCaller(expired))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Expired");
    }

    @Test
    @DisplayName("a token with no subject is rejected rather than acting anonymously")
    void rejectsMissingSubject() {
        InternalTokenService service = service(SECRET);
        String token = signer.sign(
                SECRET,
                InternalTokenService.CALLER_ISSUER,
                InternalTokenService.CALLER_AUDIENCE,
                "  ",
                List.of("employee"),
                60
        );

        assertThatThrownBy(() -> service.verifyAgentCaller(token))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no subject");
    }

    @Test
    @DisplayName("missing or malformed credentials are rejected")
    void rejectsMissingAndMalformed() {
        InternalTokenService service = service(SECRET);

        assertThatThrownBy(() -> service.verifyAgentCaller(null))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.verifyAgentCaller(""))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.verifyAgentCaller("not-a-jwt"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("with no configured secret the service refuses rather than accepting anything")
    void refusesWithoutSecret() {
        InternalTokenService service = service("   ");

        assertThatThrownBy(() -> service.verifyAgentCaller(
                mintCaller(SECRET, "user-1", List.of("employee"))
        ))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("AGENT_INTERNAL_JWT_SECRET");
    }
}
