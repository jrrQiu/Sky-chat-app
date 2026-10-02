package com.skychat.controller;

import com.skychat.service.AgentGatewayClient;
import com.skychat.service.AuthException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebInputException;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * One error shape for the whole HTTP surface.
 *
 * <p>Before this, an unexpected exception produced Spring's default error body while a handled
 * failure produced {@code {"error": "..."}} from the controller, so a client had to parse two
 * different formats. Every response here is {@code {"error": CODE, "message": ...}} with the
 * code stable enough for the front end to map to Chinese copy.</p>
 */
@RestControllerAdvice
public class ApiExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);
    public static final String INTERNAL_ERROR = "INTERNAL_ERROR";
    public static final String INVALID_REQUEST = "INVALID_REQUEST";

    @ExceptionHandler(AuthException.class)
    public ResponseEntity<Object> handleAuth(AuthException error) {
        return ApiErrors.from(error);
    }

    /**
     * The agent service is unreachable or the breaker is open. 503 rather than 500: the request
     * was well-formed and the condition is expected to be temporary.
     */
    @ExceptionHandler(AgentGatewayClient.AgentUnavailableException.class)
    public ResponseEntity<Object> handleAgentUnavailable(
            AgentGatewayClient.AgentUnavailableException error
    ) {
        return body(
                HttpStatus.SERVICE_UNAVAILABLE,
                AgentGatewayClient.UNAVAILABLE,
                error.getMessage()
        );
    }

    /** Keeps a deliberate {@code ResponseStatusException} status instead of flattening it. */
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Object> handleStatus(ResponseStatusException error) {
        HttpStatus status = HttpStatus.resolve(error.getStatusCode().value());
        return body(
                status == null ? HttpStatus.INTERNAL_SERVER_ERROR : status,
                status == null ? INTERNAL_ERROR : status.name(),
                error.getReason()
        );
    }

    /** A malformed or unparseable body is the caller's mistake, and saying so is useful. */
    @ExceptionHandler({ServerWebInputException.class, IllegalArgumentException.class})
    public ResponseEntity<Object> handleBadRequest(RuntimeException error) {
        return body(HttpStatus.BAD_REQUEST, INVALID_REQUEST, error.getMessage());
    }

    /**
     * Last resort. The message is deliberately not echoed: an internal exception can carry
     * connection strings, SQL, or file paths. The correlation detail goes to the log instead.
     */
    @ExceptionHandler(Throwable.class)
    public ResponseEntity<Object> handleUnexpected(Throwable error) {
        log.error("Unhandled request failure", error);
        return body(
                HttpStatus.INTERNAL_SERVER_ERROR,
                INTERNAL_ERROR,
                "服务内部错误，请稍后重试"
        );
    }

    private ResponseEntity<Object> body(HttpStatus status, String code, String message) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("error", code);
        payload.put("message", message == null ? code : message);
        return ResponseEntity.status(status).body(payload);
    }
}
