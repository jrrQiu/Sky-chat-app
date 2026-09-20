package com.skychat.service;

import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class RunEventStore {
    public static final class RunRecord {
        private final String id;
        private final String userId;
        private final String conversationId;
        private final List<StoredEvent> events = new CopyOnWriteArrayList<>();
        private final Sinks.Many<ServerSentEvent<String>> sink =
                Sinks.many().multicast().onBackpressureBuffer();
        private volatile String status = "running";

        private RunRecord(String id, String userId, String conversationId) {
            this.id = id;
            this.userId = userId;
            this.conversationId = conversationId;
        }

        public String id() {
            return id;
        }

        public String userId() {
            return userId;
        }

        public String conversationId() {
            return conversationId;
        }

        public String status() {
            return status;
        }

        public int lastSeq() {
            return events.isEmpty() ? 0 : events.get(events.size() - 1).seq();
        }
    }

    private record StoredEvent(int seq, String data) {
    }

    private final ConcurrentHashMap<String, RunRecord> runs = new ConcurrentHashMap<>();

    public RunRecord getOrCreate(String runId, String userId, String conversationId) {
        return runs.computeIfAbsent(runId, ignored ->
                new RunRecord(runId, userId, conversationId)
        );
    }

    public RunRecord get(String runId) {
        return runs.get(runId);
    }

    public int append(String runId, String data) {
        RunRecord run = runs.get(runId);
        if (run == null || !"running".equals(run.status())) {
            return -1;
        }

        synchronized (run) {
            int seq = run.events.size() + 1;
            run.events.add(new StoredEvent(seq, data));
            run.sink.tryEmitNext(toEvent(seq, data));
            return seq;
        }
    }

    public boolean complete(String runId) {
        RunRecord run = runs.get(runId);
        if (run == null || !"running".equals(run.status())) {
            return false;
        }

        synchronized (run) {
            if (!"running".equals(run.status())) {
                return false;
            }
            int seq = run.events.size() + 1;
            String data = "{\"type\":\"complete\"}";
            run.events.add(new StoredEvent(seq, data));
            run.status = "completed";
            run.sink.tryEmitNext(toEvent(seq, data));
            run.sink.tryEmitComplete();
            return true;
        }
    }

    public boolean fail(String runId, String message) {
        RunRecord run = runs.get(runId);
        if (run == null || !"running".equals(run.status())) {
            return false;
        }

        synchronized (run) {
            if (!"running".equals(run.status())) {
                return false;
            }
            int seq = run.events.size() + 1;
            String data = "{\"type\":\"error\",\"message\":\"" + escapeJson(message) + "\"}";
            run.events.add(new StoredEvent(seq, data));
            run.status = "failed";
            run.sink.tryEmitNext(toEvent(seq, data));
            run.sink.tryEmitComplete();
            return true;
        }
    }

    public boolean cancel(String runId) {
        RunRecord run = runs.get(runId);
        if (run == null || !"running".equals(run.status())) {
            return false;
        }

        synchronized (run) {
            if (!"running".equals(run.status())) {
                return false;
            }
            int seq = run.events.size() + 1;
            String data = "{\"type\":\"error\",\"message\":\"本次生成已取消\",\"cancelled\":true}";
            run.events.add(new StoredEvent(seq, data));
            run.status = "cancelled";
            run.sink.tryEmitNext(toEvent(seq, data));
            run.sink.tryEmitComplete();
            return true;
        }
    }

    public Flux<ServerSentEvent<String>> replay(String runId, int afterSeq) {
        RunRecord run = runs.get(runId);
        if (run == null) {
            return Flux.empty();
        }

        List<ServerSentEvent<String>> history = new ArrayList<>();
        for (StoredEvent event : run.events) {
            if (event.seq() > afterSeq) {
                history.add(toEvent(event.seq(), event.data()));
            }
        }

        Flux<ServerSentEvent<String>> historyFlux = Flux.fromIterable(history);
        if ("running".equals(run.status())) {
            return historyFlux.concatWith(run.sink.asFlux());
        }
        return historyFlux;
    }

    private ServerSentEvent<String> toEvent(int seq, String data) {
        return ServerSentEvent.<String>builder(data)
                .id(String.valueOf(seq))
                .event("message")
                .build();
    }

    private String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }
}
