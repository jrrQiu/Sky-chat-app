package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.UserAccount;
import com.skychat.domain.UserRoles;
import com.skychat.dto.UserSummary;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.UserAdminService;
import org.springframework.http.ResponseEntity;
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
 * Member management for the admin console.
 *
 * <p>Authorization happens inside {@link UserAdminService} against the roles carried by the
 * signed JWT, so every route here is safe by construction even though the checks are not
 * repeated in the annotations.</p>
 */
@RestController
@RequestMapping("/v1/admin")
public class AdminUserController {
    private final UserAdminService userAdminService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public AdminUserController(
            UserAdminService userAdminService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.userAdminService = userAdminService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    /** The roles the console may offer, in the canonical order. */
    @GetMapping("/roles")
    public Mono<ResponseEntity<Object>> roles(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader
    ) {
        return Mono.fromCallable(() -> {
                    List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
                    return ResponseEntity.ok(
                            (Object) Map.of("roles", userAdminService.assignableRoles())
                    );
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @GetMapping("/users")
    public Mono<ResponseEntity<Object>> list(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestParam(value = "query", required = false) String query,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "page", defaultValue = "0") int page,
            @RequestParam(value = "size", defaultValue = "20") int size
    ) {
        return Mono.fromCallable(() -> {
                    UserAdminService.Page result = userAdminService.search(
                            UserRoles.parseHeader(rolesHeader),
                            query,
                            status,
                            page,
                            size
                    );
                    Map<String, Object> body = new LinkedHashMap<>();
                    body.put("items", result.items().stream().map(UserSummary::of).toList());
                    body.put("total", result.total());
                    body.put("page", result.page());
                    body.put("size", result.size());
                    return ResponseEntity.ok((Object) body);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/users")
    public Mono<ResponseEntity<Object>> create(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
        String email = stringValue(body.get("email"));
        String name = stringValue(body.get("name"));
        String password = stringValue(body.get("password"));
        List<String> roles = roleList(body.get("roles"));

        return Mono.fromCallable(() -> {
                    try {
                        UserAccount created = userAdminService.createUser(
                                actorRoles, email, name, roles, password
                        );
                        audit(userId, "user.create", created.getId(), AuditService.OUTCOME_SUCCESS,
                                exchange, Map.of("email", created.getEmail(),
                                        "roles", created.getRolesList()));
                        return ResponseEntity.status(201).body((Object) UserSummary.of(created));
                    } catch (AuthException error) {
                        // A refused attempt is a security event even though it changed nothing.
                        audit(userId, "user.create", null, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("email", email == null ? "" : email,
                                        "error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PatchMapping("/users/{id}/roles")
    public Mono<ResponseEntity<Object>> changeRoles(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
        List<String> roles = roleList(body.get("roles"));

        return Mono.fromCallable(() -> {
                    try {
                        UserAccount updated = userAdminService.changeRoles(actorRoles, id, roles);
                        audit(userId, "user.roles_changed", id, AuditService.OUTCOME_SUCCESS,
                                exchange, Map.of("roles", updated.getRolesList()));
                        return ResponseEntity.ok((Object) UserSummary.of(updated));
                    } catch (AuthException error) {
                        audit(userId, "user.roles_changed", id, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("error", error.code(), "roles", roles));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @PostMapping("/users/{id}/disable")
    public Mono<ResponseEntity<Object>> disable(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            ServerWebExchange exchange
    ) {
        return setEnabled(userId, rolesHeader, id, false, exchange);
    }

    @PostMapping("/users/{id}/enable")
    public Mono<ResponseEntity<Object>> enable(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            ServerWebExchange exchange
    ) {
        return setEnabled(userId, rolesHeader, id, true, exchange);
    }

    private Mono<ResponseEntity<Object>> setEnabled(
            String userId,
            String rolesHeader,
            String id,
            boolean enabled,
            ServerWebExchange exchange
    ) {
        List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
        return Mono.fromCallable(() -> {
                    try {
                        UserAccount updated = userAdminService.setEnabled(
                                actorRoles, id, enabled, userId
                        );
                        audit(userId, enabled ? "user.enable" : "user.disable", id,
                                AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("status", updated.getStatus()));
                        return ResponseEntity.ok((Object) UserSummary.of(updated));
                    } catch (AuthException error) {
                        audit(userId, enabled ? "user.enable" : "user.disable", id,
                                AuditService.OUTCOME_DENIED, exchange,
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
                "user",
                objectId,
                outcome,
                detail
        );
    }

    private static String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    @SuppressWarnings("unchecked")
    private static List<String> roleList(Object value) {
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        if (value instanceof String text) {
            return UserRoles.parseHeader(text);
        }
        return List.of();
    }
}
