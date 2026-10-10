package com.skychat.controller;

import com.skychat.service.AgentCircuitBreaker;
import com.skychat.service.RunEventStore;
import com.skychat.service.ToolIdempotencyService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
public class HealthController {
    private final RunEventStore runEventStore;
    private final AgentCircuitBreaker breaker;
    private final ToolIdempotencyService toolIdempotencyService;

    public HealthController(
            RunEventStore runEventStore,
            AgentCircuitBreaker breaker,
            ToolIdempotencyService toolIdempotencyService
    ) {
        this.runEventStore = runEventStore;
        this.breaker = breaker;
        this.toolIdempotencyService = toolIdempotencyService;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        // All three are operational facts an operator needs and none is sensitive: how much run
        // history is resident, whether we currently believe the agent service is reachable, and
        // whether cross-service write deduplication is actually in effect. The last one matters
        // because "the agent retries are deduplicated" is a claim that is only true if the table
        // is reachable -- so the endpoint reports `active: false` with a reason rather than
        // asserting it.
        body.put("runEvents", runEventStore.stats());
        body.put("agentCircuit", breaker.snapshot());
        body.put("toolIdempotency", toolIdempotencyService.status());
        return body;
    }
}
