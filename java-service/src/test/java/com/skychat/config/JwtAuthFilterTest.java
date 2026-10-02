package com.skychat.config;

import com.skychat.domain.UserAccount;
import com.skychat.service.JwtService;
import com.skychat.support.InMemoryUserMapper;
import com.skychat.support.TestFixture;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.buffer.DefaultDataBufferFactory;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.http.server.reactive.MockServerHttpResponse;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Bearer authentication and the account-status check.
 *
 * <p>Two properties are load-bearing and easy to break by accident: a disabled account must
 * lose access immediately (otherwise the admin console's 停用 button does nothing), and the
 * public whitelist must match whole paths only (a prefix match would have opened the whole
 * invitation-management surface).</p>
 */
class JwtAuthFilterTest {

    private InMemoryUserMapper userMapper;
    private JwtService jwtService;
    private JwtAuthFilter filter;

    @BeforeEach
    void setUp() {
        userMapper = new InMemoryUserMapper();
        jwtService = TestFixture.jwtService();
        filter = new JwtAuthFilter(jwtService, userMapper);
    }

    /** Records whether the rest of the chain ran, and what it received. */
    private static final class RecordingChain implements WebFilterChain {
        private boolean continued;
        private ServerWebExchange seen;

        @Override
        public Mono<Void> filter(ServerWebExchange exchange) {
            this.continued = true;
            this.seen = exchange;
            return Mono.empty();
        }
    }

    private UserAccount account(String id, String status) {
        UserAccount user = new UserAccount();
        user.setId(id);
        user.setEmail(id + "@example.com");
        user.setName(id);
        user.setPasswordHash("hash");
        user.setRoles("it_staff");
        user.setStatus(status);
        user.setCreatedAt(LocalDateTime.now());
        userMapper.insert(user);
        return user;
    }

    private MockServerWebExchange request(String path, String token) {
        MockServerHttpRequest.BaseBuilder<?> builder = MockServerHttpRequest.get(path);
        if (token != null) {
            builder = builder.header("Authorization", "Bearer " + token);
        }
        return MockServerWebExchange.from(builder.build());
    }

    /**
     * Carries both the exchange and the concrete mock response, kept for the case where a
     * future response body needs asserting.
     *
     * <p>Note on scope: the JSON error body cannot be asserted here today. The default
     * {@link MockServerWebExchange} response throws on {@code bufferFactory().wrap(..)}, and
     * reaching for {@code mutate().response(..)} makes the headers read-only, so the filter's
     * own {@code getHeaders().set(CONTENT_TYPE, ..)} fails instead. Both are limitations of the
     * test double; these tests therefore cover the decision (which requests are refused)
     * rather than the serialisation of the refusal.</p>
     */
    private record Probe(ServerWebExchange exchange, MockServerHttpResponse response) {
    }

    private Probe probeWithReadableBody(String path, String token) {
        MockServerHttpRequest.BaseBuilder<?> builder = MockServerHttpRequest.get(path);
        if (token != null) {
            builder = builder.header("Authorization", "Bearer " + token);
        }
        MockServerHttpResponse response =
                new MockServerHttpResponse(DefaultDataBufferFactory.sharedInstance);
        ServerWebExchange exchange =
                MockServerWebExchange.from(builder).mutate().response(response).build();
        return new Probe(exchange, response);
    }

    private String tokenFor(String userId) {
        return jwtService.createToken(userId, userId + "@example.com", List.of("it_staff"));
    }

    @Test
    @DisplayName("a valid token continues the chain and exposes the identity headers")
    void validTokenIsAccepted() {
        account("alice", UserAccount.STATUS_ACTIVE);
        RecordingChain chain = new RecordingChain();

        filter.filter(request("/v1/conversations", tokenFor("alice")), chain).block();

        assertThat(chain.continued).isTrue();
        assertThat(chain.seen.getRequest().getHeaders().getFirst("X-User-ID"))
                .isEqualTo("alice");
        assertThat(chain.seen.getRequest().getHeaders().getFirst("X-User-Roles"))
                .isEqualTo("it_staff");
    }

    @Test
    @DisplayName("a missing or malformed token is 401 and never reaches the chain")
    void missingTokenIsRejected() {
        RecordingChain chain = new RecordingChain();
        filter.filter(request("/v1/conversations", null), chain).block();
        assertThat(chain.continued).isFalse();

        MockServerWebExchange malformed = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/conversations")
                        .header("Authorization", "Bearer not-a-jwt")
        );
        RecordingChain second = new RecordingChain();
        filter.filter(malformed, second).block();
        assertThat(second.continued).isFalse();
        assertThat(malformed.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    /**
     * The security property that makes the admin console's 停用 button real: a still-valid JWT
     * stops working the moment the account is disabled, without waiting for it to expire.
     *
     * <p>The {@code onErrorResume} is not a concession about the filter but about
     * {@link MockServerWebExchange}: its response hands out a read-only header view once the
     * reply is written from a non-event-loop thread, so the filter's
     * {@code getHeaders().set(CONTENT_TYPE, ..)} throws after the status has already been set.
     * The status and "the chain never ran" assertions below are therefore meaningful; the JSON
     * body itself is covered by the live end-to-end check against a real server.</p>
     */
    @Test
    @DisplayName("a disabled account is rejected immediately even with a valid token")
    void disabledAccountLosesAccessImmediately() {
        account("bob", UserAccount.STATUS_DISABLED);
        MockServerWebExchange exchange = request("/v1/conversations", tokenFor("bob"));
        RecordingChain chain = new RecordingChain();

        filter.filter(exchange, chain)
                .onErrorResume(error -> Mono.empty())
                .block();

        assertThat(chain.continued).isFalse();
        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    /**
     * The other half of "a change takes effect immediately": a role change must not have to
     * wait for the token to expire. Reading the roles from the database rather than from the
     * token claims is what makes a demotion bite on the very next request.
     */
    @Test
    @DisplayName("a role change takes effect on the next request, not at token expiry")
    void roleChangeTakesEffectImmediately() {
        UserAccount user = account("carol", UserAccount.STATUS_ACTIVE);
        String token = jwtService.createToken(
                user.getId(), user.getEmail(), List.of("admin")
        );

        // The token still claims admin, but the account was demoted in the database.
        userMapper.updateRoles(user.getId(), "employee");

        RecordingChain chain = new RecordingChain();
        filter.filter(request("/v1/admin/users", token), chain).block();

        assertThat(chain.continued).isTrue();
        assertThat(chain.seen.getRequest().getHeaders().getFirst("X-User-Roles"))
                .isEqualTo("employee");
    }

    @Test
    @DisplayName("a token for an account that no longer exists is rejected")
    void unknownAccountIsRejected() {
        RecordingChain chain = new RecordingChain();
        MockServerWebExchange exchange = request("/v1/conversations", tokenFor("ghost"));

        filter.filter(exchange, chain).block();

        assertThat(chain.continued).isFalse();
        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    @DisplayName("login, register and the two public invitation routes need no token")
    void publicRoutesAreOpen() {
        for (String path : List.of(
                "/health",
                "/v1/auth/login",
                "/v1/auth/register",
                "/v1/auth/invitations/accept",
                "/v1/auth/invitations/preview"
        )) {
            RecordingChain chain = new RecordingChain();
            filter.filter(request(path, null), chain).block();
            assertThat(chain.continued).as("public path %s", path).isTrue();
        }
    }

    /**
     * The whitelist must not be a prefix match: reading the invitation table is an admin
     * action and has to stay behind authentication.
     */
    @Test
    @DisplayName("the invitation admin routes are not public")
    void invitationAdminRoutesStayProtected() {
        for (String path : List.of(
                "/v1/auth/invitations",
                "/v1/auth/invitations/accept-extra",
                "/v1/auth/invitations/preview/all"
        )) {
            MockServerWebExchange exchange = request(path, null);
            RecordingChain chain = new RecordingChain();
            filter.filter(exchange, chain).block();
            assertThat(chain.continued).as("protected path %s", path).isFalse();
            assertThat(exchange.getResponse().getStatusCode())
                    .isEqualTo(HttpStatus.UNAUTHORIZED);
        }
    }
}
