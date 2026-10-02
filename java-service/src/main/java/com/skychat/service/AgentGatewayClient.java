package com.skychat.service;

import com.skychat.config.AgentCallProperties;
import com.skychat.config.AgentServiceProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.util.retry.Retry;

import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * The Java service's single door to the Python agent service.
 *
 * <p>Wraps the call in a circuit breaker and a deliberately narrow retry, so an unavailable
 * agent service produces a fast, explicit failure instead of a stack of connect timeouts.</p>
 */
@Service
public class AgentGatewayClient {
    private static final Logger log = LoggerFactory.getLogger(AgentGatewayClient.class);
    public static final String UNAVAILABLE = "AGENT_UNAVAILABLE";

    private final WebClient webClient;
    private final AgentServiceProperties properties;
    private final InternalTokenService internalTokenService;
    private final AgentCircuitBreaker breaker;
    private final AgentCallProperties callProperties;

    public AgentGatewayClient(
            WebClient webClient,
            AgentServiceProperties properties,
            InternalTokenService internalTokenService,
            AgentCircuitBreaker breaker,
            AgentCallProperties callProperties
    ) {
        this.webClient = webClient;
        this.properties = properties;
        this.internalTokenService = internalTokenService;
        this.breaker = breaker;
        this.callProperties = callProperties;
    }

    public Flux<ServerSentEvent<String>> stream(Map<String, Object> body, String userId) {
        return stream(body, userId, List.of());
    }

    public Flux<ServerSentEvent<String>> stream(
            Map<String, Object> body,
            String userId,
            List<String> roles
    ) {
        if (!breaker.allowRequest()) {
            log.warn("Refusing agent call: circuit is {}", breaker.snapshot().state());
            return Flux.error(new AgentUnavailableException(
                    "Agent 服务暂时不可用，请稍后重试"
            ));
        }

        WebClient.RequestBodySpec request = webClient
                .post()
                .uri(properties.url() + "/v1/chat/stream")
                // The JWT sub is the authority; this header stays as a non-authoritative hint
                // for the Python side's logging and backwards compatibility.
                .header("X-User-ID", userId)
                .header(
                        HttpHeaders.AUTHORIZATION,
                        "Bearer " + internalTokenService.createToken(userId, roles)
                )
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM);

        return request
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                })
                .retryWhen(safeRetry())
                .doOnComplete(breaker::recordSuccess)
                // A retry-exhausted failure surfaces as Reactor's "Retries exhausted: n/n", which
                // tells a user nothing. Translate transport exhaustion into the same explicit
                // unavailability the breaker produces, so the stream reports one clear reason.
                .onErrorMap(
                        error -> AgentCircuitBreaker.isTransportFailure(error)
                                && !(error instanceof AgentUnavailableException),
                        error -> new AgentUnavailableException(
                                "Agent 服务暂时不可用，请稍后重试",
                                error
                        )
                )
                .doOnError(error -> {
                    if (AgentCircuitBreaker.isTransportFailure(error)) {
                        breaker.recordFailure();
                    } else {
                        // The agent service answered, so it is healthy. A 4xx or a mid-stream
                        // application error must not open the breaker.
                        breaker.recordSuccess();
                    }
                });
    }

    /**
     * Retries only when the connection could never be established, which is the one case where
     * we know the agent service did not start processing. Everything else surfaces immediately:
     * the endpoint starts a model run, so a blind retry would duplicate work and cost.
     */
    private Retry safeRetry() {
        int attempts = Math.max(0, callProperties.retryAttempts());
        return Retry.backoff(attempts, Duration.ofMillis(Math.max(0L, callProperties.retryBackoffMs())))
                .filter(AgentCircuitBreaker::isSafeToRetry)
                .doBeforeRetry(signal -> log.warn(
                        "Retrying agent call after connection failure: {}",
                        signal.failure().toString()
                ));
    }

    public AgentCircuitBreaker.Snapshot breakerSnapshot() {
        return breaker.snapshot();
    }

    /** Raised when the breaker is open, so callers can answer 503 rather than 500. */
    public static class AgentUnavailableException extends RuntimeException {
        public AgentUnavailableException(String message) {
            super(message);
        }

        public AgentUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
