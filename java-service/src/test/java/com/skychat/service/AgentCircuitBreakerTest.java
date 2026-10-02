package com.skychat.service;

import com.skychat.config.AgentCallProperties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.io.IOException;
import java.net.ConnectException;
import java.net.UnknownHostException;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Circuit-breaker behaviour and the two classifiers that decide when it is safe to retry.
 *
 * <p>The classifiers matter more than the state machine: retrying an established-but-timed-out
 * POST would start a second model run, and counting an HTTP 4xx would open the breaker because
 * of a caller bug.</p>
 */
class AgentCircuitBreakerTest {

    private AgentCircuitBreaker newBreaker(int threshold, long openMs) {
        return new AgentCircuitBreaker(new AgentCallProperties(3000, 120000, 2, 300, threshold, openMs));
    }

    @Test
    @DisplayName("the breaker opens once consecutive failures reach the threshold")
    void opensAtThreshold() {
        AgentCircuitBreaker breaker = newBreaker(3, 30_000);

        breaker.recordFailure();
        breaker.recordFailure();
        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.CLOSED);

        breaker.recordFailure();
        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.OPEN);
        assertThat(breaker.openingCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("while open, calls are refused without being attempted")
    void openRefusesCalls() {
        AgentCircuitBreaker breaker = newBreaker(1, 30_000);
        breaker.recordFailure();

        assertThat(breaker.allowRequest(1_000)).isFalse();
        assertThat(breaker.allowRequest(1_001)).isFalse();
        assertThat(breaker.rejectionCount()).isEqualTo(2);
        // A refused call is not a failure: nothing was attempted, so the counter must not move.
        assertThat(breaker.snapshot().consecutiveFailures()).isEqualTo(1);
    }

    @Test
    @DisplayName("after the cooldown a single probe is allowed, and a success closes the breaker")
    void halfOpenProbeThenClose() {
        AgentCircuitBreaker breaker = newBreaker(1, 5_000);
        breaker.recordFailure(1_000);

        // Still inside the cooldown.
        assertThat(breaker.allowRequest(5_999)).isFalse();

        // Cooldown elapsed: exactly one probe.
        assertThat(breaker.allowRequest(6_000)).isTrue();
        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.HALF_OPEN);
        assertThat(breaker.allowRequest(6_001)).isFalse();

        breaker.recordSuccess();
        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.CLOSED);
        assertThat(breaker.snapshot().consecutiveFailures()).isZero();
        assertThat(breaker.allowRequest(6_002)).isTrue();
    }

    @Test
    @DisplayName("a failed probe reopens the breaker immediately")
    void failedProbeReopens() {
        AgentCircuitBreaker breaker = newBreaker(5, 1_000);
        for (int i = 0; i < 5; i++) {
            breaker.recordFailure(500);
        }
        assertThat(breaker.allowRequest(2_000)).isTrue();

        breaker.recordFailure(2_001);

        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.OPEN);
        assertThat(breaker.allowRequest(2_100)).isFalse();
    }

    @Test
    @DisplayName("a success resets the consecutive-failure count")
    void successResetsFailures() {
        AgentCircuitBreaker breaker = newBreaker(3, 30_000);
        breaker.recordFailure();
        breaker.recordFailure();
        breaker.recordSuccess();
        breaker.recordFailure();

        assertThat(breaker.snapshot().state()).isEqualTo(AgentCircuitBreaker.State.CLOSED);
        assertThat(breaker.snapshot().consecutiveFailures()).isEqualTo(1);
    }

    @Test
    @DisplayName("transport failures count, HTTP error statuses do not")
    void classifiesFailures() {
        assertThat(AgentCircuitBreaker.isTransportFailure(new ConnectException("refused"))).isTrue();
        assertThat(AgentCircuitBreaker.isTransportFailure(new UnknownHostException("dns"))).isTrue();
        assertThat(AgentCircuitBreaker.isTransportFailure(
                new RuntimeException(new IOException(new ConnectException("nested")))
        )).isTrue();

        WebClientResponseException notFound = WebClientResponseException.create(
                404, "Not Found", null, null, null
        );
        assertThat(AgentCircuitBreaker.isTransportFailure(notFound)).isFalse();
        // A transport wrapper around an HTTP status is still an application answer.
        assertThat(AgentCircuitBreaker.isTransportFailure(
                new RuntimeException(notFound)
        )).isFalse();
    }

    @Test
    @DisplayName("a retry is only safe when the request was never delivered")
    void retrySafetyIsNarrow() {
        // Connection never established: the agent service did not start the turn.
        assertThat(AgentCircuitBreaker.isSafeToRetry(new ConnectException("refused"))).isTrue();
        assertThat(AgentCircuitBreaker.isSafeToRetry(new UnknownHostException("dns"))).isTrue();

        // Connection established and then idle: the turn may already be running and billable.
        assertThat(AgentCircuitBreaker.isSafeToRetry(
                io.netty.handler.timeout.ReadTimeoutException.INSTANCE
        )).isFalse();
        assertThat(AgentCircuitBreaker.isSafeToRetry(new java.nio.channels.ClosedChannelException()))
                .isFalse();
        assertThat(AgentCircuitBreaker.isSafeToRetry(
                WebClientResponseException.create(500, "Server Error", null, null, null)
        )).isFalse();
        assertThat(AgentCircuitBreaker.isSafeToRetry(
                new WebClientRequestException(
                        new java.nio.channels.ClosedChannelException(),
                        org.springframework.http.HttpMethod.POST,
                        java.net.URI.create("http://localhost:8000/v1/chat/stream"),
                        org.springframework.http.HttpHeaders.EMPTY
                )
        )).isFalse();
    }
}
