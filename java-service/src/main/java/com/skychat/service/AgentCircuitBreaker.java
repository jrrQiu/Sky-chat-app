package com.skychat.service;

import com.skychat.config.AgentCallProperties;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A small circuit breaker in front of the agent service.
 *
 * <p>Without it, an agent service that is down turns every user request into a full connect
 * timeout: the caller waits seconds to be told what the process already knows. Once the failure
 * threshold is reached the breaker opens and calls fail immediately, then a single probe is
 * allowed through after the cooldown so recovery is automatic.</p>
 *
 * <p>Only transport-level failures are counted. A 4xx from the agent service is a healthy
 * service telling us we sent something wrong, and counting it would open the breaker because of
 * a client bug.</p>
 */
@Component
public class AgentCircuitBreaker {
    public enum State {
        CLOSED,
        OPEN,
        HALF_OPEN
    }

    /** Immutable view for diagnostics and tests. */
    public record Snapshot(State state, int consecutiveFailures, long openedAtMillis) {
    }

    private final AgentCallProperties properties;

    private State state = State.CLOSED;
    private int consecutiveFailures;
    private long openedAtMillis;
    /** In HALF_OPEN, only one probe may be in flight at a time. */
    private boolean probeInFlight;

    private final AtomicLong rejections = new AtomicLong();
    private final AtomicInteger openings = new AtomicInteger();

    public AgentCircuitBreaker(AgentCallProperties properties) {
        this.properties = properties;
    }

    /**
     * Whether a call may proceed right now. A call that is refused must not be recorded as a
     * failure: nothing was attempted.
     */
    public synchronized boolean allowRequest() {
        return allowRequest(System.currentTimeMillis());
    }

    synchronized boolean allowRequest(long nowMillis) {
        if (state == State.OPEN) {
            if (nowMillis - openedAtMillis >= Math.max(0L, properties.breakerOpenMs())) {
                state = State.HALF_OPEN;
                probeInFlight = true;
                return true;
            }
            rejections.incrementAndGet();
            return false;
        }
        if (state == State.HALF_OPEN) {
            if (probeInFlight) {
                rejections.incrementAndGet();
                return false;
            }
            probeInFlight = true;
            return true;
        }
        return true;
    }

    public synchronized void recordSuccess() {
        state = State.CLOSED;
        consecutiveFailures = 0;
        probeInFlight = false;
        openedAtMillis = 0L;
    }

    public synchronized void recordFailure() {
        recordFailure(System.currentTimeMillis());
    }

    synchronized void recordFailure(long nowMillis) {
        consecutiveFailures++;
        probeInFlight = false;
        if (state == State.HALF_OPEN
                || consecutiveFailures >= Math.max(1, properties.breakerFailureThreshold())) {
            if (state != State.OPEN) {
                openings.incrementAndGet();
            }
            state = State.OPEN;
            openedAtMillis = nowMillis;
        }
    }

    public synchronized Snapshot snapshot() {
        return new Snapshot(state, consecutiveFailures, openedAtMillis);
    }

    public long rejectionCount() {
        return rejections.get();
    }

    public int openingCount() {
        return openings.get();
    }

    /** Test seam: forget all history. */
    public synchronized void reset() {
        state = State.CLOSED;
        consecutiveFailures = 0;
        openedAtMillis = 0L;
        probeInFlight = false;
    }

    /**
     * Transport-level failures only: a connection that was never established, or a peer that
     * went away mid-response. An HTTP error status is excluded on purpose.
     */
    public static boolean isTransportFailure(Throwable error) {
        Throwable current = error;
        int depth = 0;
        while (current != null && depth++ < 12) {
            if (current instanceof java.net.ConnectException
                    || current instanceof java.net.UnknownHostException
                    || current instanceof java.net.SocketTimeoutException
                    || current instanceof java.nio.channels.ClosedChannelException
                    || current instanceof io.netty.handler.timeout.ReadTimeoutException
                    || current instanceof io.netty.handler.timeout.TimeoutException
                    || current instanceof org.springframework.web.reactive.function.client
                            .WebClientRequestException) {
                return true;
            }
            if (current instanceof org.springframework.web.reactive.function.client
                    .WebClientResponseException) {
                return false;
            }
            current = current.getCause();
        }
        return false;
    }

    /**
     * Whether a retry is safe. The chat endpoint is a POST that starts a model run, so a retry
     * is only acceptable when the request provably never reached the agent service — i.e. the
     * TCP connection could not be established. Retrying an established-but-timed-out request
     * could run the turn twice and bill it twice.
     */
    public static boolean isSafeToRetry(Throwable error) {
        Throwable current = error;
        int depth = 0;
        while (current != null && depth++ < 12) {
            if (current instanceof java.net.ConnectException
                    || current instanceof java.net.UnknownHostException
                    || current instanceof java.net.SocketTimeoutException) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }
}
