package com.skychat;

import com.skychat.config.AgentInternalProperties;
import com.skychat.config.AgentServiceProperties;
import com.skychat.config.AuthProperties;
import com.skychat.config.RateLimitProperties;
import com.skychat.config.SecurityProperties;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@MapperScan("com.skychat.mapper")
@EnableConfigurationProperties({
        AgentServiceProperties.class,
        AgentInternalProperties.class,
        AuthProperties.class,
        SecurityProperties.class,
        RateLimitProperties.class
})
@EnableScheduling
public class SkyChatJavaServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(SkyChatJavaServiceApplication.class, args);
    }
}
