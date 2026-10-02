package com.skychat;

import com.skychat.config.AgentCallProperties;
import com.skychat.config.AgentInternalProperties;
import com.skychat.config.AgentServiceProperties;
import com.skychat.config.AuthProperties;
import com.skychat.config.RateLimitProperties;
import com.skychat.config.RunEventProperties;
import com.skychat.config.SecurityProperties;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * {@code @ConfigurationProperties} records are not picked up by component scanning, so every new
 * one has to be listed here. Forgetting means the context fails at startup with "no qualifying
 * bean", which is how {@link AgentCallProperties} and {@link RunEventProperties} were caught.
 */
@SpringBootApplication
@MapperScan("com.skychat.mapper")
@EnableConfigurationProperties({
        AgentServiceProperties.class,
        AgentInternalProperties.class,
        AuthProperties.class,
        SecurityProperties.class,
        RateLimitProperties.class,
        AgentCallProperties.class,
        RunEventProperties.class
})
@EnableScheduling
public class SkyChatJavaServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(SkyChatJavaServiceApplication.class, args);
    }
}
