package com.skychat.controller;

import com.skychat.domain.Message;
import com.skychat.service.MessageService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/v1/conversations/{conversationId}/messages")
public class MessageController {
    private final MessageService messageService;

    public MessageController(MessageService messageService) {
        this.messageService = messageService;
    }

    @GetMapping
    public Mono<ResponseEntity<List<Message>>> list(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String conversationId
    ) {
        return Mono.fromCallable(() -> messageService.list(userId, conversationId))
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    @PostMapping
    public Mono<ResponseEntity<Object>> create(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String conversationId,
            @RequestBody Map<String, String> body
    ) {
        return Mono.<ResponseEntity<Object>>fromCallable(() -> {
                    Message message = messageService.create(
                            userId,
                            conversationId,
                            body.getOrDefault("role", "user"),
                            body.getOrDefault("content", "")
                    );
                    if (message == null) {
                        return ResponseEntity.<Object>notFound().build();
                    }
                    return ResponseEntity.<Object>ok(message);
                })
                .subscribeOn(Schedulers.boundedElastic());
    }
}
