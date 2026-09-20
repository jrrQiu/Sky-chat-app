package com.skychat.controller;

import com.skychat.service.RunEventStore;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.util.Map;

@RestController
@RequestMapping("/v1/chat/runs")
public class RunController {
    private final RunEventStore runEventStore;

    public RunController(RunEventStore runEventStore) {
        this.runEventStore = runEventStore;
    }

    @GetMapping(path = "/{runId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Mono<ResponseEntity<Flux<ServerSentEvent<String>>>> replay(
            @RequestHeader("X-User-ID") String userId,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId,
            @RequestParam(name = "lastSeq", required = false) Integer lastSeq,
            @PathVariable String runId
    ) {
        RunEventStore.RunRecord run = runEventStore.get(runId);
        if (run == null || !run.userId().equals(userId)) {
            return Mono.just(ResponseEntity.notFound().build());
        }

        int afterSeq = Math.max(0, lastSeq != null ? lastSeq : parseAfterSeq(lastEventId));
        return Mono.just(ResponseEntity.ok()
                .header(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform")
                .header("X-Run-ID", run.id())
                .header("X-Run-Status", run.status())
                .header("X-Run-Last-Seq", String.valueOf(run.lastSeq()))
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .body(runEventStore.replay(runId, afterSeq)));
    }

    @DeleteMapping("/{runId}")
    public Mono<ResponseEntity<Map<String, Object>>> cancel(
            @RequestHeader("X-User-ID") String userId,
            @PathVariable String runId
    ) {
        RunEventStore.RunRecord run = runEventStore.get(runId);
        if (run == null || !run.userId().equals(userId)) {
            return Mono.just(ResponseEntity.notFound().build());
        }

        boolean cancelled = runEventStore.cancel(runId);
        return Mono.just(ResponseEntity.ok(Map.of(
                "success", cancelled,
                "cancelled", cancelled
        )));
    }

    private int parseAfterSeq(String value) {
        if (value == null || value.isBlank()) {
            return 0;
        }
        try {
            return Math.max(0, Integer.parseInt(value));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }
}
