package com.skychat;

import com.skychat.config.AgentServiceProperties;
import com.skychat.config.AuthProperties;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@MapperScan("com.skychat.mapper")
@EnableConfigurationProperties({AgentServiceProperties.class, AuthProperties.class})
public class SkyChatJavaServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(SkyChatJavaServiceApplication.class, args);
    }
}
