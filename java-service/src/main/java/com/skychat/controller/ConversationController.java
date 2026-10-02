package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.Conversation;
import com.skychat.service.AuditService;
import com.skychat.service.ConversationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/v1/conversations")
public class ConversationController {
    private final ConversationService conversationService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public ConversationController(
            ConversationService conversationService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.conversationService = conversationService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    @GetMapping
    public Mono<ResponseEntity<List<Conversation>>> list(
            @RequestHeader("X-User-ID") String userId
    ) {
        return Mono.fromCallable(() -> conversationService.list(userId))
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    @PostMapping
    public Mono<ResponseEntity<Conversation>> create(
            @RequestHeader("X-User-ID") String userId,
            @RequestBody Map<String, String> body
    ) {
        return Mono.fromCallable(() -> conversationService.create(userId, body.get("title")))
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    @DeleteMapping("/{id}")
    public Mono<ResponseEntity<Map<String, Boolean>>> delete(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String id,
            ServerWebExchange exchange
    ) {
        return Mono.fromCallable(() -> {
                    boolean deleted = conversationService.delete(userId, id);
                    // Deleting a conversation removes its messages: an administrative action
                    // worth an audit row, unlike reading the conversation list.
                    auditService.record(
                            AuditService.Context.user(
                                    userId,
                                    clientAddressResolver.resolve(exchange.getRequest()),
                                    null
                            ),
                            "conversation.delete",
                            "conversation",
                            id,
                            deleted ? AuditService.OUTCOME_SUCCESS : AuditService.OUTCOME_FAILURE,
                            Map.of("deleted", deleted)
                    );
                    return ResponseEntity.ok(Map.of("deleted", deleted));
                })
                .subscribeOn(Schedulers.boundedElastic());
    }

    @PatchMapping("/{id}")
    public Mono<ResponseEntity<Conversation>> updateTitle(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String id,
            @RequestBody Map<String, String> body
    ) {
        return Mono.fromCallable(() -> {
                    String title = body.get("title");
                    if (!conversationService.updateTitle(id, userId, title)) {
                        return null;
                    }
                    return conversationService.findById(id, userId);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .map(conversation -> conversation == null
                        ? ResponseEntity.notFound().<Conversation>build()
                        : ResponseEntity.ok(conversation));
    }
}
