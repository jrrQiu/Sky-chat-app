package com.skychat.controller;

import com.skychat.dto.ChatStreamRequest;
import com.skychat.service.ChatStreamService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.Arrays;
import java.util.List;

@RestController
public class AgentProxyController {
    private final ChatStreamService chatStreamService;

    public AgentProxyController(ChatStreamService chatStreamService) {
        this.chatStreamService = chatStreamService;
    }

    @PostMapping(
            path = {"/v1/agent/chat/stream", "/v1/chat/stream"},
            produces = MediaType.TEXT_EVENT_STREAM_VALUE
    )
    public Mono<ResponseEntity<Flux<ServerSentEvent<String>>>> stream(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId,
            @RequestBody ChatStreamRequest request
    ) {
        String runId = ChatStreamService.normalizeRunId(request.getRunId());
        int afterSeq = parseAfterSeq(lastEventId);
        List<String> roles = rolesHeader == null || rolesHeader.isBlank()
                ? List.of("employee")
                : Arrays.stream(rolesHeader.split(","))
                        .map(String::trim)
                        .filter(value -> !value.isBlank())
                        .toList();

        return Mono.just(ResponseEntity.ok()
                .header(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform")
                .header("X-Run-ID", runId)
                .header("X-Conversation-ID", request.getConversationId() == null
                        ? ""
                        : request.getConversationId())
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .body(chatStreamService.stream(
                        request,
                        userId,
                        roles,
                        afterSeq,
                        runId,
                        request.getConversationId()
                )));
    }

    private int parseAfterSeq(String value) {
        if (value == null || value.isBlank()) {
            return 0;
        }
        try {
            return Math.max(0, Integer.parseInt(value));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }
}
