package com.skychat.controller;

import com.skychat.service.MessageService;
import org.springframework.http.ResponseEntity;
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
@RequestMapping("/v1/messages")
public class MessageMutationController {
    private final MessageService messageService;

    public MessageMutationController(MessageService messageService) {
        this.messageService = messageService;
    }

    @PostMapping("/delete")
    public Mono<ResponseEntity<Map<String, Object>>> delete(
            @RequestHeader("X-User-ID") String userId,
            @RequestBody Map<String, List<String>> body
    ) {
        return Mono.fromCallable(() -> {
                    int deleted = messageService.deleteByIds(userId, body.get("messageIds"));
                    return Map.<String, Object>of("deleted", deleted);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }
}
