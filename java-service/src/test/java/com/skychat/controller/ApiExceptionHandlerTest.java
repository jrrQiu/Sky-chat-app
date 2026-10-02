package com.skychat.controller;

import com.skychat.service.AgentGatewayClient;
import com.skychat.service.AuthService;
import com.skychat.service.AuthException;
import com.skychat.service.UserAdminService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebInputException;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The shared error contract: every failure is {@code {"error": CODE, "message": ...}} with a
 * status a client can branch on.
 */
class ApiExceptionHandlerTest {

    private final ApiExceptionHandler handler = new ApiExceptionHandler();

    @SuppressWarnings("unchecked")
    private Map<String, Object> bodyOf(ResponseEntity<Object> response) {
        return (Map<String, Object>) response.getBody();
    }

    @Test
    @DisplayName("a security rejection keeps its code and maps to the agreed status")
    void mapsAuthExceptions() {
        ResponseEntity<Object> forbidden = handler.handleAuth(
                new AuthException(AdminAuthorizerCodes.FORBIDDEN_NOT_ADMIN, "需要管理员权限")
        );
        assertThat(forbidden.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN);
        assertThat(bodyOf(forbidden)).containsEntry("error", "FORBIDDEN_NOT_ADMIN");

        ResponseEntity<Object> conflict = handler.handleAuth(
                new AuthException(UserAdminService.LAST_ADMIN, "不能移除最后一个管理员的权限")
        );
        assertThat(conflict.getStatusCode()).isEqualTo(HttpStatus.CONFLICT);

        ResponseEntity<Object> notFound = handler.handleAuth(
                new AuthException(UserAdminService.NOT_FOUND, "用户不存在")
        );
        assertThat(notFound.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);

        ResponseEntity<Object> disabled = handler.handleAuth(
                new AuthException(AuthService.ACCOUNT_DISABLED, "账号已停用")
        );
        assertThat(disabled.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    @DisplayName("an unreachable agent service is 503, not 500")
    void mapsAgentUnavailableTo503() {
        ResponseEntity<Object> response = handler.handleAgentUnavailable(
                new AgentGatewayClient.AgentUnavailableException("Agent 服务暂时不可用，请稍后重试")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(bodyOf(response)).containsEntry("error", AgentGatewayClient.UNAVAILABLE);
    }

    @Test
    @DisplayName("a malformed body is reported as the caller's mistake")
    void mapsBadRequests() {
        ResponseEntity<Object> response = handler.handleBadRequest(
                new ServerWebInputException("Required request body is missing")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(bodyOf(response)).containsEntry("error", ApiExceptionHandler.INVALID_REQUEST);
    }

    @Test
    @DisplayName("a deliberate ResponseStatusException keeps its own status")
    void keepsDeliberateStatus() {
        ResponseEntity<Object> response = handler.handleStatus(
                new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS, "slow down")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.TOO_MANY_REQUESTS);
        assertThat(bodyOf(response)).containsEntry("message", "slow down");
    }

    @Test
    @DisplayName("an unexpected failure is 500 and never echoes the internal message")
    void unexpectedFailureDoesNotLeak() {
        ResponseEntity<Object> response = handler.handleUnexpected(
                new IllegalStateException("jdbc:postgresql://user:pw@host/db connection refused")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
        assertThat(bodyOf(response)).containsEntry("error", ApiExceptionHandler.INTERNAL_ERROR);
        assertThat(String.valueOf(bodyOf(response).get("message")))
                .doesNotContain("jdbc:postgresql")
                .doesNotContain("pw@host");
    }

    /** Local mirror of the constant so the test does not depend on the authorizer's shape. */
    private static final class AdminAuthorizerCodes {
        static final String FORBIDDEN_NOT_ADMIN = "FORBIDDEN_NOT_ADMIN";
    }
}
