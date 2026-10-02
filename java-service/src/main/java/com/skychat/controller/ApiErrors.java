package com.skychat.controller;

import com.skychat.service.AuthException;
import com.skychat.service.AuthService;
import com.skychat.service.InvitationService;
import com.skychat.service.UserAdminService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.util.Map;

/**
 * Maps a {@link AuthException} onto the HTTP contract the admin UI expects.
 *
 * <p>Centralised so the two new controllers cannot disagree about, say, whether an expired
 * invitation is a 400 or a 409. The body is always {@code {"error": "<CODE>"}}; the front end
 * keys its Chinese copy off that code rather than off the message.</p>
 */
final class ApiErrors {
    private ApiErrors() {
    }

    static ResponseEntity<Object> from(AuthException error) {
        return ResponseEntity.status(statusFor(error.code()))
                .body(Map.of("error", error.code(), "message", error.getMessage()));
    }

    static HttpStatus statusFor(String code) {
        return switch (code) {
            case "FORBIDDEN_NOT_ADMIN", "FORBIDDEN_ROLE_ESCALATION" -> HttpStatus.FORBIDDEN;
            // "Cannot be used any more" is a conflict with current state, not a bad request.
            case InvitationService.INVITATION_EXPIRED,
                 InvitationService.INVITATION_ALREADY_ACCEPTED,
                 InvitationService.INVITATION_REVOKED,
                 UserAdminService.LAST_ADMIN,
                 UserAdminService.CANNOT_DISABLE_SELF -> HttpStatus.CONFLICT;
            case UserAdminService.NOT_FOUND,
                 InvitationService.NOT_FOUND -> HttpStatus.NOT_FOUND;
            case AuthService.ACCOUNT_DISABLED -> HttpStatus.UNAUTHORIZED;
            case AuthService.SELF_REGISTRATION_DISABLED -> HttpStatus.FORBIDDEN;
            default -> HttpStatus.BAD_REQUEST;
        };
    }
}
