package com.skychat.config;

import io.netty.channel.ChannelOption;
import io.netty.handler.timeout.ReadTimeoutHandler;
import io.netty.handler.timeout.WriteTimeoutHandler;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

import java.util.concurrent.TimeUnit;

/**
 * The shared {@link WebClient}.
 *
 * <p>Timeouts are attached here rather than per call site so no future caller can forget them:
 * before this, a hung agent service could hold a connection indefinitely, and the OS default
 * connect timeout is measured in tens of seconds.</p>
 */
@Configuration
public class WebClientConfig {
    @Bean
    public WebClient webClient(WebClient.Builder builder, AgentCallProperties properties) {
        HttpClient httpClient = HttpClient.create()
                .option(
                        ChannelOption.CONNECT_TIMEOUT_MILLIS,
                        Math.max(500, properties.connectTimeoutMs())
                )
                .responseTimeout(java.time.Duration.ofMillis(
                        Math.max(1_000, properties.idleTimeoutMs())
                ))
                .doOnConnected(connection -> connection
                        .addHandlerLast(new ReadTimeoutHandler(
                                Math.max(1_000, properties.idleTimeoutMs()),
                                TimeUnit.MILLISECONDS
                        ))
                        .addHandlerLast(new WriteTimeoutHandler(
                                Math.max(1_000, properties.idleTimeoutMs()),
                                TimeUnit.MILLISECONDS
                        )));

        return builder
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }
}
