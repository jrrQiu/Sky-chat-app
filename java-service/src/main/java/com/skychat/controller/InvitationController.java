package com.skychat.controller;

import com.skychat.config.ClientAddressResolver;
import com.skychat.domain.UserInvitation;
import com.skychat.domain.UserRoles;
import com.skychat.dto.InvitationSummary;
import com.skychat.service.AuditService;
import com.skychat.service.AuthException;
import com.skychat.service.AuthService;
import com.skychat.service.InvitationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
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
 * Invitations: the onboarding path that replaces open self-registration.
 *
 * <p>{@code preview} and {@code accept} are deliberately unauthenticated — the invitee has
 * no account yet — and are therefore also the only two routes here that must stay cheap and
 * heavily rate limited. Both are whitelisted in the auth filter.</p>
 */
@RestController
@RequestMapping("/v1/auth/invitations")
public class InvitationController {
    private final InvitationService invitationService;
    private final AuditService auditService;
    private final ClientAddressResolver clientAddressResolver;

    public InvitationController(
            InvitationService invitationService,
            AuditService auditService,
            ClientAddressResolver clientAddressResolver
    ) {
        this.invitationService = invitationService;
        this.auditService = auditService;
        this.clientAddressResolver = clientAddressResolver;
    }

    /**
     * Issues an invitation. The response is the only place the raw token ever appears, so the
     * UI must show the link immediately.
     */
    @PostMapping
    public Mono<ResponseEntity<Object>> create(
            @RequestHeader("X-User-ID") String actorId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
        String email = stringValue(body.get("email"));
        String name = stringValue(body.get("name"));
        List<String> roles = roleList(body.get("roles"));
        Integer ttlHours = intValue(body.get("ttlHours"));

        return Mono.fromCallable(() -> {
                    try {
                        InvitationService.IssuedInvitation issued = invitationService.create(
                                email, name, roles, ttlHours, actorId
                        );
                        UserInvitation invitation = issued.invitation();
                        String inviteUrl = invitationService.inviteUrl(issued.rawToken());
                        audit(actorId, "invitation.send", invitation.getId(),
                                AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("email", invitation.getEmail(),
                                        "roles", invitation.getRolesList(),
                                        "expiresAt", String.valueOf(invitation.getExpiresAt())));
                        return ResponseEntity.status(201).body((Object) InvitationSummary.of(
                                invitation, inviteUrl
                        ));
                    } catch (AuthException error) {
                        audit(actorId, "invitation.send", null, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("error", error.code(),
                                        "email", email == null ? "" : email));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @GetMapping
    public Mono<ResponseEntity<Object>> list(
            @RequestHeader("X-User-ID") String actorId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @RequestParam(value = "status", required = false) String status,
            @RequestParam(value = "limit", defaultValue = "100") int limit
    ) {
        return Mono.fromCallable(() -> {
                    // Authorisation is implicit: a non-admin has no business reading the
                    // invitation table, so reuse the service's admin gate through a cheap probe.
                    invitationService.assertCanAdminister(UserRoles.parseHeader(rolesHeader));
                    List<InvitationSummary> items = invitationService.list(status, limit).stream()
                            .map(InvitationSummary::of)
                            .toList();
                    return ResponseEntity.ok((Object) items);
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    @DeleteMapping("/{id}")
    public Mono<ResponseEntity<Object>> revoke(
            @RequestHeader("X-User-ID") String actorId,
            @RequestHeader(value = "X-User-Roles", required = false) String rolesHeader,
            @PathVariable String id,
            ServerWebExchange exchange
    ) {
        List<String> actorRoles = UserRoles.parseHeader(rolesHeader);
        return Mono.fromCallable(() -> {
                    try {
                        invitationService.assertCanAdminister(actorRoles);
                        invitationService.revoke(id);
                        audit(actorId, "invitation.revoke", id, AuditService.OUTCOME_SUCCESS,
                                exchange, Map.of());
                        return ResponseEntity.noContent().build();
                    } catch (AuthException error) {
                        audit(actorId, "invitation.revoke", id, AuditService.OUTCOME_DENIED,
                                exchange, Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    /**
     * Read-only lookup for the set-password page. Returns the invited address so the invitee
     * can confirm the link is theirs, and a reason when it can no longer be used.
     */
    @GetMapping("/preview")
    public Mono<ResponseEntity<Object>> preview(@RequestParam("token") String token) {
        return Mono.fromCallable(() -> {
                    InvitationService.Preview preview = invitationService.preview(token);
                    Map<String, Object> body = new LinkedHashMap<>();
                    body.put("email", preview.email());
                    body.put("name", preview.name());
                    body.put("roles", preview.roles());
                    body.put("expiresAt", preview.expiresAt());
                    body.put("valid", preview.valid());
                    body.put("reason", preview.reason());
                    return ResponseEntity.ok((Object) body);
                })
                .subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * Consumes the invitation: sets the password and returns a session, so the invitee lands
     * signed in.
     */
    @PostMapping("/accept")
    public Mono<ResponseEntity<Object>> accept(
            @RequestBody Map<String, Object> body,
            ServerWebExchange exchange
    ) {
        String token = stringValue(body.get("token"));
        String password = stringValue(body.get("password"));
        String name = stringValue(body.get("name"));

        return Mono.fromCallable(() -> {
                    try {
                        AuthService.AuthSession session =
                                invitationService.accept(token, password, name);
                        audit(session.user().getId(), "invitation.accept", session.user().getId(),
                                AuditService.OUTCOME_SUCCESS, exchange,
                                Map.of("email", session.user().getEmail(),
                                        "roles", session.user().getRolesList()));
                        return ResponseEntity.ok((Object) sessionResponse(session));
                    } catch (AuthException error) {
                        audit(null, "invitation.accept", null, AuditService.OUTCOME_FAILURE,
                                exchange, Map.of("error", error.code()));
                        throw error;
                    }
                })
                .subscribeOn(Schedulers.boundedElastic())
                .onErrorResume(AuthException.class, error -> Mono.just(ApiErrors.from(error)));
    }

    /** Mirrors the login response shape so the front end can reuse its session handling. */
    private Map<String, Object> sessionResponse(AuthService.AuthSession session) {
        Map<String, Object> user = new LinkedHashMap<>();
        user.put("id", session.user().getId());
        user.put("email", session.user().getEmail());
        user.put("name", session.user().getName());
        user.put("roles", session.user().getRolesList());

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("token", session.token());
        response.put("user", user);
        return response;
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
                "invitation",
                objectId,
                outcome,
                detail
        );
    }

    private static String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private static Integer intValue(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Integer.valueOf(text.trim());
            } catch (NumberFormatException error) {
                return null;
            }
        }
        return null;
    }

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
