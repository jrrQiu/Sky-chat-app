package com.skychat.service;

import com.skychat.config.AgentServiceProperties;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.util.Map;

@Service
public class AgentGatewayClient {
    private final WebClient webClient;
    private final AgentServiceProperties properties;

    public AgentGatewayClient(
            WebClient webClient,
            AgentServiceProperties properties
    ) {
        this.webClient = webClient;
        this.properties = properties;
    }

    public Flux<ServerSentEvent<String>> stream(Map<String, Object> body, String userId) {
        WebClient.RequestBodySpec request = webClient
                .post()
                .uri(properties.url() + "/v1/chat/stream")
                .header("X-User-ID", userId)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM);

        if (properties.token() != null && !properties.token().isBlank()) {
            request.header(HttpHeaders.AUTHORIZATION, "Bearer " + properties.token());
        }

        return request
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                });
    }
}
