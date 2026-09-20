package com.skychat.controller;

import com.skychat.domain.ApprovalTask;
import com.skychat.service.ApprovalService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/v1/approvals")
public class ApprovalController {
    private final ApprovalService approvalService;

    public ApprovalController(ApprovalService approvalService) {
        this.approvalService = approvalService;
    }

    @GetMapping
    public Mono<ResponseEntity<List<ApprovalTask>>> list(
            @RequestHeader("X-User-ID") String userId
    ) {
        return Mono.fromCallable(() -> approvalService.list(userId))
                .subscribeOn(Schedulers.boundedElastic())
                .map(ResponseEntity::ok);
    }

    @PatchMapping("/{id}/decision")
    public Mono<ResponseEntity<Object>> decide(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String id,
            @RequestBody Map<String, String> body
    ) {
        return Mono.<ResponseEntity<Object>>fromCallable(() -> {
                    String action = body.getOrDefault("action", "");
                    if (!"approve".equals(action) && !"reject".equals(action)) {
                        return ResponseEntity.<Object>badRequest().body(Map.of("error", "invalid action"));
                    }

                    ApprovalTask task = approvalService.decide(userId, id, action);
                    if (task == null) {
                        return ResponseEntity.<Object>notFound().build();
                    }
                    return ResponseEntity.<Object>ok(task);
                })
                .subscribeOn(Schedulers.boundedElastic());
    }
}
