package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.service.AuditService;
import com.skychat.service.MessageService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/v1/messages")
public class MessageMutationController {
    private final MessageService messageService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public MessageMutationController(
            MessageService messageService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.messageService = messageService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    @PostMapping("/delete")
    public Mono<ResponseEntity<Map<String, Object>>> delete(
            @RequestHeader("X-User-ID") String userId,
            @RequestBody Map<String, List<String>> body,
            ServerWebExchange exchange
    ) {
        List<String> messageIds = body.get("messageIds");
        return Mono.fromCallable(() -> {
                    int deleted = messageService.deleteByIds(userId, messageIds);
                    // Deleting history is destructive and worth a trail; listing messages is not.
                    Map<String, Object> detail = new LinkedHashMap<>();
                    detail.put("deleted", deleted);
                    detail.put("requested", messageIds == null ? 0 : messageIds.size());
                    auditService.record(
                            AuditService.Context.user(
                                    userId,
                                    clientAddressResolver.resolve(exchange.getRequest()),
                                    null
                            ),
                            "message.delete",
                            "message",
                            messageIds == null || messageIds.size() != 1 ? null : messageIds.get(0),
                            AuditService.OUTCOME_SUCCESS,
                            detail
                    );
                    return ResponseEntity.ok(Map.<String, Object>of("deleted", deleted));
                })
                .subscribeOn(Schedulers.boundedElastic());
    }
}
