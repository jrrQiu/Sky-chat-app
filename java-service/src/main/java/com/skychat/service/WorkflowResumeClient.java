package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.config.AgentServiceProperties;
import com.skychat.domain.ApprovalTask;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Calls the Python agent service resume endpoint defined by the frozen cross-service protocol.
 *
 * <p>The method never emits an error signal; every failure is classified into a
 * {@link ResumeResult} so the caller can decide whether a retry is safe.</p>
 */
@Service
public class WorkflowResumeClient {
    /**
     * A hung agent must not pin a request thread or a scheduled tick forever.
     */
    private static final Duration RESUME_TIMEOUT = Duration.ofSeconds(120);

    private final WebClient webClient;
    private final AgentServiceProperties properties;
    private final ObjectMapper objectMapper;
    private final InternalTokenService internalTokenService;

    public WorkflowResumeClient(
            WebClient webClient,
            AgentServiceProperties properties,
            ObjectMapper objectMapper,
            InternalTokenService internalTokenService
    ) {
        this.webClient = webClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.internalTokenService = internalTokenService;
    }

    public enum Outcome {
        /**
         * The agent applied the decision, or already applied this exact resume_request_id.
         */
        SUCCEEDED,
        /**
         * The checkpoint is gone or stale (HTTP 409) - never retry automatically.
         */
        STALE,
        /**
         * The agent does not know this approval (HTTP 404) - never retry automatically.
         */
        NOT_FOUND,
        /**
         * 5xx, timeout, connection error or any other transport failure - retryable.
         */
        TRANSIENT_FAILURE
    }

    public record ResumeResult(Outcome outcome, String status, String error) {
        public boolean succeeded() {
            return outcome == Outcome.SUCCEEDED;
        }

        public boolean stale() {
            return outcome == Outcome.STALE;
        }

        public boolean notFound() {
            return outcome == Outcome.NOT_FOUND;
        }

        public boolean transientFailure() {
            return outcome == Outcome.TRANSIENT_FAILURE;
        }
    }

    public Mono<ResumeResult> resume(
            ApprovalTask task,
            String decision,
            String resumeRequestId
    ) {
        String requestId = resumeRequestId == null || resumeRequestId.isBlank()
                ? task.getResumeRequestId()
                : resumeRequestId;
        if (requestId == null || requestId.isBlank()) {
            // Without a stable id the agent cannot deduplicate a retry. Treat it as
            // retryable rather than poisoning the approval as stale.
            return Mono.just(new ResumeResult(
                    Outcome.TRANSIENT_FAILURE,
                    null,
                    "MISSING_RESUME_REQUEST_ID"
            ));
        }

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("decision", decision);
        payload.put("approval_id", task.getId());
        payload.put("checkpoint_id", task.getCheckpointId());
        payload.put("checkpoint_ns", task.getCheckpointNs());
        payload.put("interrupt_id", task.getInterruptId());
        payload.put("resume_request_id", requestId);

        String actingUser = task.getUserId() == null ? "" : task.getUserId();
        WebClient.RequestBodySpec request = webClient
                .post()
                .uri(properties.url() + "/v1/workflows/{turnId}/resume", task.getTurnId())
                .header("X-User-ID", actingUser)
                // A freshly minted short-lived internal JWT per call; the static
                // AGENT_SERVICE_TOKEN is no longer the credential.
                .header(
                        HttpHeaders.AUTHORIZATION,
                        "Bearer " + internalTokenService.createToken(actingUser, List.of())
                )
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON);

        return request
                .bodyValue(payload)
                .retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {
                })
                .defaultIfEmpty(Map.of())
                .timeout(RESUME_TIMEOUT)
                .map(this::classifySuccess)
                .onErrorResume(error -> Mono.just(classifyError(error)));
    }

    private ResumeResult classifySuccess(Map<String, Object> body) {
        Object rawStatus = body.get("status");
        String status = rawStatus == null ? "resumed" : String.valueOf(rawStatus);
        if ("resumed".equals(status) || "already_resumed".equals(status)) {
            return new ResumeResult(Outcome.SUCCEEDED, status, null);
        }
        // An unexpected 200 payload is retried with the same resume_request_id, which the
        // agent deduplicates, so this is safe.
        return new ResumeResult(
                Outcome.TRANSIENT_FAILURE,
                status,
                "UNEXPECTED_RESUME_STATUS:" + status
        );
    }

    private ResumeResult classifyError(Throwable error) {
        if (error instanceof WebClientResponseException response) {
            int status = response.getStatusCode().value();
            if (status == 409) {
                return new ResumeResult(Outcome.STALE, null, detailOf(response));
            }
            if (status == 404) {
                return new ResumeResult(Outcome.NOT_FOUND, null, detailOf(response));
            }
            return new ResumeResult(Outcome.TRANSIENT_FAILURE, null, messageOf(error));
        }
        return new ResumeResult(Outcome.TRANSIENT_FAILURE, null, messageOf(error));
    }

    private String detailOf(WebClientResponseException response) {
        String body = response.getResponseBodyAsString();
        if (body != null && !body.isBlank()) {
            try {
                JsonNode node = objectMapper.readTree(body);
                String detail = node.path("detail").asText();
                if (detail != null && !detail.isBlank()) {
                    return detail;
                }
            } catch (Exception ignored) {
                // Fall through to the raw body below.
            }
            return body.trim();
        }
        return response.getStatusCode().toString();
    }

    private String messageOf(Throwable error) {
        String message = error.getMessage();
        return message == null || message.isBlank()
                ? error.getClass().getSimpleName()
                : message;
    }
}
