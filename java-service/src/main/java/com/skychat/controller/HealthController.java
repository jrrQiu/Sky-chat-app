package com.skychat.controller;

import com.skychat.service.AgentCircuitBreaker;
import com.skychat.service.RunEventStore;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
public class HealthController {
    private final RunEventStore runEventStore;
    private final AgentCircuitBreaker breaker;

    public HealthController(RunEventStore runEventStore, AgentCircuitBreaker breaker) {
        this.runEventStore = runEventStore;
        this.breaker = breaker;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        // Both are operational facts an operator needs and neither is sensitive: how much run
        // history is resident, and whether we currently believe the agent service is reachable.
        body.put("runEvents", runEventStore.stats());
        body.put("agentCircuit", breaker.snapshot());
        return body;
    }
}
