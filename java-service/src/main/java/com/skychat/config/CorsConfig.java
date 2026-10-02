package com.skychat.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.config.CorsRegistry;
import org.springframework.web.reactive.config.WebFluxConfigurer;

@Configuration
public class CorsConfig implements WebFluxConfigurer {
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(
                        "http://localhost:5173",
                        "http://127.0.0.1:5173",
                        "http://localhost:5174",
                        "http://127.0.0.1:5174",
                        "http://localhost:5175",
                        "http://127.0.0.1:5175"
                )
                .allowedMethods("GET", "POST", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("*")
                .exposedHeaders(
                        "X-Run-ID",
                        "X-Run-Status",
                        "X-Run-Last-Seq",
                        "X-Conversation-ID",
                        "X-Conversation-Title"
                )
                .allowCredentials(true);
    }
}
