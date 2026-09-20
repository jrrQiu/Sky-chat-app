package com.skychat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.domain.Conversation;
import com.skychat.dto.ChatMessageInput;
import com.skychat.dto.ChatStreamRequest;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

@Service
public class ChatStreamService {
    private final AgentGatewayClient agentGatewayClient;
    private final MessageService messageService;
    private final ConversationService conversationService;
    private final RunEventStore runEventStore;
    private final ObjectMapper objectMapper;

    public ChatStreamService(
            AgentGatewayClient agentGatewayClient,
            MessageService messageService,
            ConversationService conversationService,
            RunEventStore runEventStore,
            ObjectMapper objectMapper
    ) {
        this.agentGatewayClient = agentGatewayClient;
        this.messageService = messageService;
        this.conversationService = conversationService;
        this.runEventStore = runEventStore;
        this.objectMapper = objectMapper;
    }

    public Flux<ServerSentEvent<String>> stream(
            ChatStreamRequest request,
            String userId,
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

            messageService.create(userId, resolvedConversationId, "user", latestUserMessage);

            AtomicReference<String> assistantText = new AtomicReference<>("");
            AtomicReference<String> title = new AtomicReference<>("");
            AtomicBoolean failed = new AtomicBoolean(false);

            String finalConversationId = resolvedConversationId;
            Map<String, Object> agentPayload = buildAgentPayload(
                    request,
                    userId,
                    runId,
                    finalConversationId,
                    latestUserMessage
            );

            return agentGatewayClient.stream(agentPayload, userId)
                    .map(event -> {
                        String data = event.data() == null ? "" : event.data();
                        int seq = runEventStore.append(runId, data);
                        accumulateEvent(data, assistantText, title, failed);
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
                                assistantText.get(),
                                title.get()
                        );
                    })
                    .doOnError(error -> {
                        String message = error instanceof Exception
                                ? error.getMessage()
                                : String.valueOf(error);
                        runEventStore.fail(runId, message);
                    })
                    .subscribeOn(Schedulers.boundedElastic());
        });
    }

    private Map<String, Object> buildAgentPayload(
            ChatStreamRequest request,
            String userId,
            String runId,
            String conversationId,
            String latestUserMessage
    ) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("request_id", runId);
        payload.put("user_context", Map.of("userId", userId));
        payload.put("conversation_id", conversationId);
        payload.put("user_message_id", request.getUserMessageId());
        payload.put("assistant_message_id", request.getAiMessageId());
        payload.put("messages", request.getMessages());
        payload.put("latest_user_message", latestUserMessage);
        payload.put("model", request.getModel());
        payload.put("api_key", "");
        payload.put("enable_thinking", request.isEnableThinking());
        payload.put("enable_web_search", request.isEnableWebSearch());
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

        List<ChatMessageInput> messages = request.getMessages();
        for (int index = messages.size() - 1; index >= 0; index -= 1) {
            ChatMessageInput message = messages.get(index);
            if ("user".equals(message.getRole()) && message.getContent() != null) {
                return message.getContent().trim();
            }
        }
        return "";
    }

    private void accumulateEvent(
            String data,
            AtomicReference<String> assistantText,
            AtomicReference<String> title,
            AtomicBoolean failed
    ) {
        try {
            JsonNode event = objectMapper.readTree(data);
            String type = event.path("type").asText();
            if ("answer".equals(type) && event.has("content")) {
                assistantText.set(assistantText.get() + event.get("content").asText());
            } else if ("conversation_title".equals(type) && event.has("content")) {
                title.set(event.get("content").asText());
            } else if ("error".equals(type)) {
                failed.set(true);
            }
        } catch (Exception ignored) {
            // Non-JSON events do not affect answer persistence.
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

    public static String normalizeRunId(String runId) {
        if (runId != null && !runId.isBlank()) {
            return runId.trim();
        }
        return "run_" + UUID.randomUUID();
    }
}
