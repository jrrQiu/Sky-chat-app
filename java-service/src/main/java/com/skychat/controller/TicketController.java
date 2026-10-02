package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.UserRoles;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.TicketService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Service-desk tickets.
 *
 * <p>Authorization lives in {@link TicketService}: the controller only translates transport
 * concerns and records the administrative audit trail. In particular the controller does not
 * decide visibility — a non-agent's list is narrowed in SQL by the service.</p>
 */
@RestController
@RequestMapping("/v1/tickets")
public class TicketController {
    private final TicketService ticketService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public TicketController(
            TicketService ticketService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.ticketService = ticketService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    private TicketService.Caller caller(String userId, String rolesHeader, String actorType) {
        List<String> roles = UserRoles.parseHeader(rolesHeader);
        return new TicketService.Caller(userId, roles);
    }

    @GetMapping("/meta")
    public Mono<ResponseEntity<Object>> meta() {
        return Mono.fromCallable(() -> ResponseEntity.ok((Object) ticketService.meta()))
                .subscribeOn(Schedulers.boundedElastic());
    }

    @GetMapping
    public Mono<ResponseEntity<Object>> list(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestParam(value = "query", required = false) String query,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "type", required = false) String type,
            @RequestParam(value = "priority", required = false) String priority,
            @RequestParam(value = "assignee", required = false) String assignee,
            @RequestParam(value = "requester", required = false) String requester,
            @RequestParam(value = "includeMerged", defaultValue = "false") boolean includeMerged,
            @RequestParam(value = "page", defaultValue = "0") int page,
            @RequestParam(value = "size", defaultValue = "20") int size
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    TicketService.Page result = ticketService.list(
                            actor,
                            query,
                            status,
                            type,
                            priority,
                            resolveSelf(assignee, actor.userId()),
                            resolveSelf(requester, actor.userId()),
                            includeMerged,
                            page,
                            size
                    );
                    Map<String, Object> body = new LinkedHashMap<>();
                    body.put("items", result.items());
                    body.put("total", result.total());
                    body.put("page", result.page());
                    body.put("size", result.size());
                    Map<String, Object> counts = new LinkedHashMap<>();
                    counts.put("byStatus", result.byStatus());
                    counts.put("byPriority", result.byPriority());
                    body.put("counts", counts);
                    return ResponseEntity.ok((Object) body);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    /** {@code me} is resolved server-side so the UI cannot be tricked into another user's id. */
    private String resolveSelf(String value, String userId) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return "me".equalsIgnoreCase(value.trim()) ? userId : value.trim();
    }

    @PostMapping
    public Mono<ResponseEntity<Object>> create(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var created = ticketService.create(
                                actor,
                                string(body.get("type")),
                                string(body.get("title")),
                                string(body.get("description")),
                                string(body.get("impact")),
                                string(body.get("urgency")),
                                string(body.get("priority")),
                                string(body.get("assigneeId")),
                                string(body.get("parentId")),
                                string(body.get("requesterId")),
                                string(body.get("source")),
                                string(body.get("conversationId"))
                        );
                        audit(userId, "ticket.create", created.id(), AuditService.OUTCOME_SUCCESS,
                                exchange, Map.of("number", created.number(),
                                        "type", String.valueOf(created.type()),
                                        "priority", String.valueOf(created.priority())));
                        return ResponseEntity.status(201).body((Object) created);
                    } catch (AuthException error) {
                        audit(userId, "ticket.create", null, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @GetMapping("/{id}")
    public Mono<ResponseEntity<Object>> get(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> ResponseEntity.ok((Object) ticketService.get(actor, id)))
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PatchMapping("/{id}")
    public Mono<ResponseEntity<Object>> update(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var updated = ticketService.update(
                                actor,
                                id,
                                string(body.get("title")),
                                string(body.get("description")),
                                string(body.get("type")),
                                string(body.get("impact")),
                                string(body.get("urgency")),
                                string(body.get("priority")),
                                string(body.get("assigneeId"))
                        );
                        audit(userId, "ticket.update", id, AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("assigneeId", String.valueOf(updated.assigneeId()),
                                        "priority", String.valueOf(updated.priority())));
                        return ResponseEntity.ok((Object) updated);
                    } catch (AuthException error) {
                        audit(userId, "ticket.update", id, AuditService.OUTCOME_DENIED, exchange,
                                Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/{id}/transition")
    public Mono<ResponseEntity<Object>> transition(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var updated = ticketService.transition(
                                actor,
                                id,
                                string(body.get("status")),
                                string(body.get("comment")),
                                string(body.get("resolution"))
                        );
                        audit(userId, "ticket.transition", id, AuditService.OUTCOME_SUCCESS,
                                exchange, Map.of("status", String.valueOf(updated.status())));
                        return ResponseEntity.ok((Object) updated);
                    } catch (AuthException error) {
                        audit(userId, "ticket.transition", id, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("error", error.code(),
                                        "requested", String.valueOf(body.get("status"))));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/{id}/comments")
    public Mono<ResponseEntity<Object>> comment(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var updated = ticketService.addComment(
                                actor, id, string(body.get("body")), string(body.get("visibility"))
                        );
                        // An internal note is an administrative action worth auditing; a public
                        // reply is ordinary conversation and is not (Zammad's rule).
                        if ("internal".equalsIgnoreCase(string(body.get("visibility")))) {
                            audit(userId, "ticket.comment.internal", id,
                                    AuditService.OUTCOME_SUCCESS, exchange, Map.of());
                        }
                        return ResponseEntity.status(201).body((Object) updated);
                    } catch (AuthException error) {
                        audit(userId, "ticket.comment", id, AuditService.OUTCOME_DENIED, exchange,
                                Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/{id}/links")
    public Mono<ResponseEntity<Object>> link(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var updated = ticketService.addLink(
                                actor, id, string(body.get("targetId")), string(body.get("linkType"))
                        );
                        audit(userId, "ticket.link", id, AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("linkType", String.valueOf(body.get("linkType")),
                                        "targetId", String.valueOf(body.get("targetId"))));
                        return ResponseEntity.status(201).body((Object) updated);
                    } catch (AuthException error) {
                        audit(userId, "ticket.link", id, AuditService.OUTCOME_DENIED, exchange,
                                Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @DeleteMapping("/{id}/links/{linkId}")
    public Mono<ResponseEntity<Object>> unlink(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @PathVariable String linkId
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() ->
                        ResponseEntity.ok((Object) ticketService.removeLink(actor, id, linkId)))
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/{id}/merge")
    public Mono<ResponseEntity<Object>> merge(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var result = ticketService.merge(actor, id, string(body.get("targetId")));
                        audit(userId, "ticket.merge", id, AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("targetId", String.valueOf(body.get("targetId"))));
                        return ResponseEntity.ok((Object) result);
                    } catch (AuthException error) {
                        audit(userId, "ticket.merge", id, AuditService.OUTCOME_DENIED, exchange,
                                Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/{id}/split")
    public Mono<ResponseEntity<Object>> split(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = caller(userId, rolesHeader, "user");
        return Mono.fromCallable(() -> {
                    try {
                        var created = ticketService.split(
                                actor,
                                id,
                                string(body.get("commentId")),
                                string(body.get("title")),
                                string(body.get("description"))
                        );
                        audit(userId, "ticket.split", id, AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("newTicketId", created.id(),
                                        "newNumber", created.number()));
                        return ResponseEntity.status(201).body((Object) created);
                    } catch (AuthException error) {
                        audit(userId, "ticket.split", id, AuditService.OUTCOME_DENIED, exchange,
                                Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    private void audit(
            String actorId,
            String action,
            String objectId,
            String outcome,
            ServerWebExchange exchange,
            Map<String, Object> detail
    ) {
        auditService.record(
                AuditService.Context.user(
                        actorId,
                        clientAddressResolver.resolve(exchange.getRequest()),
                        exchange.getRequest().getHeaders().getFirst("User-Agent")
                ),
                action,
                "ticket",
                objectId,
                outcome,
                detail
        );
    }

    private static String string(Object value) {
        return value == null ? null : String.valueOf(value);
    }
}
