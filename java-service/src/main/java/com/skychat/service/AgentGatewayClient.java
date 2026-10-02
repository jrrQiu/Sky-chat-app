package com.skychat.service;

import com.skychat.config.AgentServiceProperties;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.util.List;
import java.util.Map;

@Service
public class AgentGatewayClient {
    private final WebClient webClient;
    private final AgentServiceProperties properties;
    private final InternalTokenService internalTokenService;

    public AgentGatewayClient(
            WebClient webClient,
            AgentServiceProperties properties,
            InternalTokenService internalTokenService
    ) {
        this.webClient = webClient;
        this.properties = properties;
        this.internalTokenService = internalTokenService;
    }

    public Flux<ServerSentEvent<String>> stream(Map<String, Object> body, String userId) {
        return stream(body, userId, List.of());
    }

    public Flux<ServerSentEvent<String>> stream(
            Map<String, Object> body,
            String userId,
            List<String> roles
    ) {
        WebClient.RequestBodySpec request = webClient
                .post()
                .uri(properties.url() + "/v1/chat/stream")
                // The JWT sub is the authority; this header stays as a non-authoritative hint
                // for the Python side's logging and backwards compatibility.
                .header("X-User-ID", userId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + internalTokenService.createToken(userId, roles))
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM);

        return request
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                });
    }
}
