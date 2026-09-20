package com.skychat.controller;

import com.skychat.domain.UserAccount;
import com.skychat.service.AuthService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/v1/auth")
public class AuthController {
    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @PostMapping("/register")
    public Mono<ResponseEntity<Map<String, Object>>> register(@RequestBody Map<String, String> body) {
        return Mono.fromCallable(() -> authService.register(
                        body.get("email"),
                        body.get("password"),
                        body.get("name")
                ))
                .subscribeOn(Schedulers.boundedElastic())
                .map(session -> ResponseEntity.ok(toResponse(session)))
                .onErrorResume(error -> Mono.just(errorResponse(error)));
    }

    @PostMapping("/login")
    public Mono<ResponseEntity<Map<String, Object>>> login(@RequestBody Map<String, String> body) {
        return Mono.fromCallable(() -> authService.login(
                        body.get("email"),
                        body.get("password")
                ))
                .subscribeOn(Schedulers.boundedElastic())
                .map(session -> ResponseEntity.ok(toResponse(session)))
                .onErrorResume(error -> Mono.just(errorResponse(error)));
    }

    @GetMapping("/me")
    public Mono<ResponseEntity<UserAccount>> me(@RequestHeader("X-User-ID") String userId) {
        return Mono.fromCallable(() -> authService.findById(userId))
                .subscribeOn(Schedulers.boundedElastic())
                .map(user -> user == null
                        ? ResponseEntity.notFound().<UserAccount>build()
                        : ResponseEntity.ok(user));
    }

    private Map<String, Object> toResponse(AuthService.AuthSession session) {
        UserAccount user = session.user();
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("token", session.token());
        response.put("user", Map.of(
                "id", user.getId(),
                "email", user.getEmail(),
                "name", user.getName()
        ));
        return response;
    }

    private ResponseEntity<Map<String, Object>> errorResponse(Throwable error) {
        String message = error instanceof IllegalArgumentException
                ? error.getMessage()
                : "请求失败，请稍后重试";
        return ResponseEntity.badRequest().body(Map.of("error", message));
    }
}
