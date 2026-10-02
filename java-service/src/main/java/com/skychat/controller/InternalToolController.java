package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.dto.TicketDetail;
import com.skychat.dto.TicketSummary;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.InternalTokenService;
import com.skychat.service.TicketService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Tool endpoints the Python agent service calls back into.
 *
 * <p>Why this exists instead of letting the agent service call {@code /v1/tickets} directly:
 * every ticket rule — visibility (404 rather than 403), the internal-note filter, the state
 * machine, the impact/urgency matrix — lives in {@link TicketService}. Re-implementing those in
 * Python would create a second, inevitably divergent policy point, and having the agent service
 * write the tables itself would create a second write path. So the agent calls here, Java applies
 * the same rules, and the acting user comes from the signed token rather than a header.</p>
 *
 * <p><strong>Credential.</strong> This path is not behind {@code JwtAuthFilter} because it does
 * not carry an end-user token: the caller presents a short-lived token minted by the agent
 * service with {@code iss=sky-chat-agent-service}, {@code aud=sky-chat-java-service}. That is the
 * mirror of the pair Java mints, and {@link InternalTokenService#verifyAgentCaller} requires both
 * claims, so neither direction's token can be replayed against the other.</p>
 *
 * <p><strong>Failure shape.</strong> A business refusal (forbidden, not found, illegal
 * transition) is a normal tool outcome and is returned as HTTP 200 with
 * {@code success=false}, because the agent should be able to tell the user what happened rather
 * than treat it as a transport failure. Only a broken credential is an HTTP error.</p>
 */
@RestController
@RequestMapping("/v1/internal/tools")
public class InternalToolController {
    private static final Logger log = LoggerFactory.getLogger(InternalToolController.class);
    private static final int MAX_LIST = 20;
    private static final int SUMMARY_COMMENTS = 3;

    private final InternalTokenService internalTokenService;
    private final TicketService ticketService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public InternalToolController(
            InternalTokenService internalTokenService,
            TicketService ticketService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.internalTokenService = internalTokenService;
        this.ticketService = ticketService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    @PostMapping("/{toolName}")
    public Mono<ResponseEntity<Object>> invoke(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @PathVariable String toolName,
            @RequestBody(required = false) Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        InternalTokenService.AgentCaller caller;
        try {
            caller = internalTokenService.verifyAgentCaller(bearer(authorization));
        } catch (IllegalArgumentException | IllegalStateException error) {
            // A broken credential is not a tool outcome: it means the integration is misconfigured,
            // and the agent must not silently continue with an anonymous identity.
            log.warn("Rejected internal tool call {}: {}", toolName, error.getMessage());
            return Mono.just(ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of(
                    "error", "INVALID_INTERNAL_CALLER",
                    "message", String.valueOf(error.getMessage())
            )));
        }

        Map<String, Object> args = unwrapArgs(body);
        return Mono.fromCallable(() -> invokeTool(toolName, args, caller, exchange))
                .subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Accepts both the generic adapter envelope {@code {"args": {...}, "state": {...}}} and a
     * bare argument object, so the same endpoint works for the first-party adapter and for
     * anything using the generic OpenAPI convention.
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> unwrapArgs(Map<String, Object> body) {
        if (body == null) {
            return Map.of();
        }
        Object nested = body.get("args");
        if (nested instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return body;
    }

    private ResponseEntity<Object> invokeTool(
            String toolName,
            Map<String, Object> args,
            InternalTokenService.AgentCaller caller,
            ServerWebExchange exchange
    ) {
        TicketService.Caller actor = new TicketService.Caller(caller.userId(), caller.roles());
        try {
            return switch (toolName) {
                case "ticket_create" -> ticketCreate(actor, args, caller, exchange);
                case "ticket_get" -> ticketGet(actor, args);
                case "ticket_list" -> ticketList(actor, args);
                case "ticket_comment" -> ticketComment(actor, args, caller, exchange);
                default -> ok(failure("未知工具：" + toolName));
            };
        } catch (AuthException error) {
            // A refusal the user should hear about, not an integration fault.
            log.info(
                    "Tool {} refused for user {}: {}",
                    toolName, caller.userId(), error.code()
            );
            return ok(failure(error.getMessage(), error.code()));
        }
    }

    private ResponseEntity<Object> ticketCreate(
            TicketService.Caller actor,
            Map<String, Object> args,
            InternalTokenService.AgentCaller caller,
            ServerWebExchange exchange
    ) {
        TicketDetail created = ticketService.create(
                actor,
                text(args.get("type"), "incident"),
                text(args.get("title"), null),
                text(args.get("description"), ""),
                text(args.get("impact"), "medium"),
                text(args.get("urgency"), "medium"),
                // The agent may not set priority: the matrix owns it, and a model choosing
                // "critical" for a printer jam is exactly the failure mode the matrix prevents.
                null,
                null,
                null,
                null,
                "chat",
                text(args.get("conversationId"), null)
        );
        audit(caller.userId(), "ticket.create", created.id(), "success", exchange, Map.of(
                "origin", "agent",
                "tool", "ticket_create",
                "number", created.number(),
                "priority", String.valueOf(created.priority())
        ));
        return ok(success(
                "已创建工单 #" + created.number() + "：" + created.title()
                        + "（类型 " + created.type() + "，优先级 " + created.priority()
                        + "，状态 " + created.status() + "）",
                ticketData(created)
        ));
    }

    private ResponseEntity<Object> ticketGet(
            TicketService.Caller actor,
            Map<String, Object> args
    ) {
        String id = resolveTicketId(actor, args);
        if (id == null) {
            return ok(failure("需要提供 ticketId 或 number"));
        }
        TicketDetail detail = ticketService.get(actor, id);
        return ok(success(describe(detail), ticketData(detail)));
    }

    private ResponseEntity<Object> ticketList(
            TicketService.Caller actor,
            Map<String, Object> args
    ) {
        TicketService.Page page = ticketService.list(
                actor,
                null,
                text(args.get("status"), null),
                text(args.get("type"), null),
                null,
                null,
                // The caller's own tickets. Resolved to the user id here rather than passing the
                // string "me": TicketService compares this value against requester_id in SQL, so
                // the literal would silently match nothing.
                actor.userId(),
                false,
                0,
                clamp(integer(args.get("limit"), 5), 1, MAX_LIST)
        );
        if (page.items().isEmpty()) {
            return ok(success("没有查询到工单。", Map.of("total", 0, "items", List.of())));
        }
        StringBuilder builder = new StringBuilder("共 " + page.total() + " 张工单：");
        List<Map<String, Object>> items = new ArrayList<>();
        for (TicketSummary item : page.items()) {
            builder.append("\n#").append(item.number())
                    .append(" [").append(item.status()).append("] ")
                    .append(item.title());
            items.add(ticketData(item));
        }
        return ok(success(builder.toString(), Map.of("total", page.total(), "items", items)));
    }

    private ResponseEntity<Object> ticketComment(
            TicketService.Caller actor,
            Map<String, Object> args,
            InternalTokenService.AgentCaller caller,
            ServerWebExchange exchange
    ) {
        String id = resolveTicketId(actor, args);
        if (id == null) {
            return ok(failure("需要提供 ticketId 或 number"));
        }
        TicketDetail updated = ticketService.addComment(
                actor,
                id,
                text(args.get("body"), null),
                text(args.get("visibility"), "public")
        );
        audit(caller.userId(), "ticket.comment", id, "success", exchange, Map.of(
                "origin", "agent",
                "tool", "ticket_comment"
        ));
        return ok(success("已回复工单 #" + updated.number() + "。", ticketData(updated)));
    }

    /** Accepts either the opaque id or the human number, because a user says "#12". */
    private String resolveTicketId(TicketService.Caller actor, Map<String, Object> args) {
        String id = text(args.get("ticketId"), null);
        if (id != null && !id.isBlank()) {
            return id.trim();
        }
        Integer number = integer(args.get("number"), null);
        if (number == null) {
            return null;
        }
        // Resolve through the visible list so an invisible ticket stays invisible: looking a
        // number up by id and then failing with "forbidden" would confirm it exists.
        TicketService.Page page = ticketService.list(
                actor, String.valueOf(number), null, null, null, null, null, false, 0, 1
        );
        return page.items().isEmpty() ? null : page.items().get(0).id();
    }

    private String describe(TicketDetail detail) {
        StringBuilder builder = new StringBuilder();
        builder.append("#").append(detail.number())
                .append(" [").append(detail.status()).append("] ")
                .append(detail.title())
                .append("\n类型 ").append(detail.type())
                .append("，优先级 ").append(detail.priority())
                .append("，申请人 ").append(detail.requesterId());
        if (detail.assigneeId() != null) {
            builder.append("，处理人 ").append(detail.assigneeId());
        }
        if (detail.description() != null && !detail.description().isBlank()) {
            builder.append("\n描述：").append(truncate(detail.description(), 300));
        }
        if (detail.resolution() != null && !detail.resolution().isBlank()) {
            builder.append("\n结论：").append(truncate(detail.resolution(), 200));
        }
        // Only what the caller may see: the service already filtered internal notes out.
        int shown = 0;
        for (var comment : detail.comments()) {
            if (shown++ >= SUMMARY_COMMENTS) {
                break;
            }
            builder.append("\n- ").append(comment.authorId())
                    .append("：").append(truncate(comment.body(), 200));
        }
        return builder.toString();
    }

    private Map<String, Object> ticketData(TicketSummary summary) {
        return ticketData(
                summary.id(),
                summary.number(),
                summary.type(),
                summary.status(),
                summary.priority(),
                summary.title(),
                summary.assigneeId()
        );
    }

    private Map<String, Object> ticketData(TicketDetail detail) {
        Map<String, Object> data = ticketData(
                detail.id(),
                detail.number(),
                detail.type(),
                detail.status(),
                detail.priority(),
                detail.title(),
                detail.assigneeId()
        );
        data.put("commentCount", detail.comments().size());
        return data;
    }

    /** Both views are flat, so the shared shape is built once from primitives. */
    private Map<String, Object> ticketData(
            String id,
            long number,
            String type,
            String status,
            String priority,
            String title,
            String assigneeId
    ) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", id);
        data.put("number", number);
        data.put("type", type);
        data.put("status", status);
        data.put("priority", priority);
        data.put("title", title);
        if (assigneeId != null) {
            data.put("assigneeId", assigneeId);
        }
        return data;
    }

    private ResponseEntity<Object> ok(Map<String, Object> payload) {
        return ResponseEntity.ok((Object) payload);
    }

    private Map<String, Object> success(String text, Map<String, Object> data) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("success", true);
        result.put("text", text);
        result.put("data", data);
        return result;
    }

    private Map<String, Object> failure(String text) {
        return failure(text, null);
    }

    private Map<String, Object> failure(String text, String code) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("success", false);
        result.put("text", text == null ? "工具调用失败" : text);
        if (code != null) {
            result.put("error", code);
        }
        return result;
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

    private String bearer(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return null;
        }
        return authorization.substring(7);
    }

    private String text(Object value, String fallback) {
        if (value == null) {
            return fallback;
        }
        String text = String.valueOf(value);
        return text.isBlank() ? fallback : text;
    }

    private Integer integer(Object value, Integer fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String string && !string.isBlank()) {
            try {
                return Integer.valueOf(string.trim());
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }
        return fallback;
    }

    private int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }

    private String truncate(String value, int max) {
        if (value == null) {
            return "";
        }
        return value.length() <= max ? value : value.substring(0, max) + "…";
    }
}
