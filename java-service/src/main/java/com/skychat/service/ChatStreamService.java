package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.Conversation;
import com.skychat.domain.Message;
import com.skychat.dto.ChatStreamRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class ChatStreamService {
    private static final Logger log = LoggerFactory.getLogger(ChatStreamService.class);

    private final AgentGatewayClient agentGatewayClient;
    private final MessageService messageService;
    private final ConversationService conversationService;
    private final ConversationContextService conversationContextService;
    private final ConversationSummaryService conversationSummaryService;
    private final RunEventStore runEventStore;
    private final ApprovalIngestService approvalIngestService;
    private final ObjectMapper objectMapper;

    public ChatStreamService(
            AgentGatewayClient agentGatewayClient,
            MessageService messageService,
            ConversationService conversationService,
            ConversationContextService conversationContextService,
            ConversationSummaryService conversationSummaryService,
            RunEventStore runEventStore,
            ApprovalIngestService approvalIngestService,
            ObjectMapper objectMapper
    ) {
        this.agentGatewayClient = agentGatewayClient;
        this.messageService = messageService;
        this.conversationService = conversationService;
        this.conversationContextService = conversationContextService;
        this.conversationSummaryService = conversationSummaryService;
        this.runEventStore = runEventStore;
        this.approvalIngestService = approvalIngestService;
        this.objectMapper = objectMapper;
    }

    public Flux<ServerSentEvent<String>> stream(
            ChatStreamRequest request,
            String userId,
            List<String> roles,
            int afterSeq,
            String runId,
            String conversationId
    ) {
        return Flux.defer(() -> {
            if (runEventStore.get(runId) != null) {
                return runEventStore.replay(runId, afterSeq);
            }

            runEventStore.getOrCreate(runId, userId, conversationId);

            String latestUserMessage = latestUserMessage(request);
            if (latestUserMessage.isBlank()) {
                throw new IllegalArgumentException("消息内容不能为空");
            }

            String resolvedConversationId = conversationId;
            if (resolvedConversationId == null || resolvedConversationId.isBlank()) {
                Conversation conversation = conversationService.create(userId, "新对话");
                resolvedConversationId = conversation.getId();
            } else if (conversationService.findById(resolvedConversationId, userId) == null) {
                Conversation conversation = conversationService.create(userId, "新对话");
                resolvedConversationId = conversation.getId();
            }

            Message userMessage = messageService.create(
                    userId,
                    resolvedConversationId,
                    "user",
                    latestUserMessage
            );

            // One accumulator per stream: it is the single place that reads the
            // agent service's event types, so the protocol rules live in one
            // testable class instead of in this lambda.
            StreamEventAccumulator accumulator = new StreamEventAccumulator(objectMapper);

            String finalConversationId = resolvedConversationId;
            Map<String, Object> agentPayload = buildAgentPayload(
                    request,
                    userId,
                    roles,
                    runId,
                    finalConversationId,
                    latestUserMessage,
                    userMessage == null ? request.getUserMessageId() : userMessage.getId()
            );

            return agentGatewayClient.stream(agentPayload, userId, roles)
                    .map(event -> {
                        String data = event.data() == null ? "" : event.data();
                        int seq = runEventStore.append(runId, data);
                        accumulator.accept(data);
                        // Runs on boundedElastic, so blocking JDBC ingestion is acceptable.
                        approvalIngestService.record(
                                userId,
                                runId,
                                finalConversationId,
                                data
                        );
                        return ServerSentEvent.<String>builder(data)
                                .id(String.valueOf(Math.max(seq, 0)))
                                .event("message")
                                .build();
                    })
                    .doOnComplete(() -> {
                        runEventStore.complete(runId);
                        persistAssistantTurn(
                                userId,
                                finalConversationId,
                                accumulator.assistantText(),
                                accumulator.title()
                        );
                        persistContextReport(finalConversationId, accumulator.contextReport());
                    })
                    .onErrorResume(error -> {
                        String message = safeErrorMessage(error);
                        runEventStore.fail(runId, message);
                        persistAssistantTurn(
                                userId,
                                finalConversationId,
                                accumulator.assistantText(),
                                accumulator.title()
                        );
                        return Flux.just(createErrorEvent(message));
                    })
                    .subscribeOn(Schedulers.boundedElastic());
        });
    }

    private String safeErrorMessage(Throwable error) {
        if (error == null) {
            return "Agent 服务调用失败";
        }
        String message = error.getMessage();
        return message == null || message.isBlank()
                ? error.getClass().getSimpleName()
                : message;
    }

    private ServerSentEvent<String> createErrorEvent(String message) {
        try {
            String data = objectMapper.writeValueAsString(Map.of(
                    "type", "error",
                    "message", message
            ));
            return ServerSentEvent.<String>builder(data)
                    .event("message")
                    .build();
        } catch (Exception error) {
            return ServerSentEvent.<String>builder(
                            "{\"type\":\"error\",\"message\":\"Agent 服务调用失败\"}"
                    )
                    .event("message")
                    .build();
        }
    }

    private Map<String, Object> buildAgentPayload(
            ChatStreamRequest request,
            String userId,
            List<String> roles,
            String runId,
            String conversationId,
            String latestUserMessage,
            String userMessageId
    ) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("request_id", runId);
        payload.put("user_context", Map.of(
                "userId", userId,
                "roles", roles == null ? List.of("employee") : roles
        ));
        payload.put("conversation_id", conversationId);
        // The agent service requires both ids. They are correlation handles, so a
        // caller that omits one gets a generated value rather than an opaque 422
        // surfaced as "Agent 服务调用失败".
        payload.put("user_message_id", fallbackId(userMessageId));
        payload.put("assistant_message_id", fallbackId(request.getAiMessageId()));
        payload.put("latest_user_message", latestUserMessage);
        payload.put("model", request.getModel());
        payload.put("api_key", "");
        payload.put("enable_thinking", request.isEnableThinking());
        payload.put("enable_web_search", request.isEnableWebSearch());
        payload.put("context_envelope", conversationContextService.build(
                userId,
                conversationId
        ));
        payload.put("agent_state", Map.of(
                "requestId", runId,
                "userContext", Map.of("userId", userId),
                "status", "processing"
        ));
        return payload;
    }

    private String latestUserMessage(ChatStreamRequest request) {
        if (request.getContent() != null && !request.getContent().isBlank()) {
            return request.getContent().trim();
        }
        return "";
    }

    /**
     * Persist what the context engine did with this turn.
     *
     * <p>The rolling summary is written here rather than by a separate per-turn job
     * because the evicted range ends at the reply the engine just produced; a
     * separate job would have to guess the range, which is how a summary and the
     * originals end up overlapping or leaving a gap. Failures are logged and never
     * propagated: the answer has already been streamed successfully.
     */
    private void persistContextReport(String conversationId, JsonNode report) {
        if (report == null || report.isNull() || conversationId == null) {
            return;
        }
        try {
            JsonNode delta = report.path("summary_delta");
            if (!delta.isMissingNode() && !delta.isNull()) {
                conversationSummaryService.persistRolling(conversationId, delta);
            }
            // Structured diagnostics for "why did the model not know that": the
            // numbers are per turn, so a support engineer can reconstruct the
            // prompt's shape without the prompt itself.
            log.debug(
                    "context_report conversation={} used={}/{} droppedMessages={} droppedEvidence={}",
                    conversationId,
                    report.path("used_tokens").asInt(0),
                    report.path("usable_tokens").asInt(0),
                    report.path("messages").path("dropped").asInt(0),
                    report.path("evidence").path("dropped").asInt(0)
            );
        } catch (RuntimeException error) {
            log.warn("Could not persist the context report for {}", conversationId, error);
        }
    }

    private void persistAssistantTurn(
            String userId,
            String conversationId,
            String assistantText,
            String title
    ) {
        if (assistantText != null && !assistantText.isBlank()) {
            messageService.create(userId, conversationId, "assistant", assistantText);
        }

        conversationService.touch(conversationId, userId);
        if (title != null && !title.isBlank() && !"新对话".equals(title)) {
            conversationService.updateTitle(conversationId, userId, title);
        }
    }

    /** The agent service requires a non-blank correlation id for both roles. */
    private static String fallbackId(String value) {
        return value == null || value.isBlank() ? "msg_" + UUID.randomUUID() : value;
    }

    public static String normalizeRunId(String runId) {        if (runId != null && !runId.isBlank()) {
            return runId.trim();
        }
        return "run_" + UUID.randomUUID();
    }
}
