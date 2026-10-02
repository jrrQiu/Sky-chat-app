package com.skychat.service;

import com.skychat.config.RunEventProperties;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * In-process buffer of run events, so a dropped SSE connection can resume with
 * {@code Last-Event-ID}.
 *
 * <p><strong>Scope and its honest limit.</strong> The buffer is per JVM: with more than one
 * replica, a client that reconnects to a different instance cannot replay its run, and a cancel
 * issued on one instance does not reach a run executing on another. Fixing that needs a shared
 * store (Redis Streams keyed by run id); it is not attempted here because pretending to support
 * multi-replica resume with in-process state would be worse than documenting the constraint.
 * What this class does guarantee is that memory stays bounded: a TTL for finished runs, a cap on
 * retained runs, and a cap on events per run.</p>
 */
@Service
public class RunEventStore {
    private static final Logger log = LoggerFactory.getLogger(RunEventStore.class);

    public static final class RunRecord {
        private final String id;
        private final String userId;
        private final String conversationId;
        private final long createdAtMillis;
        private final List<StoredEvent> events = new CopyOnWriteArrayList<>();
        private final Sinks.Many<ServerSentEvent<String>> sink =
                Sinks.many().multicast().onBackpressureBuffer();
        private volatile String status = "running";
        private volatile long finishedAtMillis;
        /** Monotonic, so sequence numbers stay unique even after the oldest events are dropped. */
        private int nextSeq = 1;
        /** Lowest sequence still buffered; anything below it was dropped by the per-run cap. */
        private volatile int truncatedBefore;

        private RunRecord(String id, String userId, String conversationId, long createdAtMillis) {
            this.id = id;
            this.userId = userId;
            this.conversationId = conversationId;
            this.createdAtMillis = createdAtMillis;
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

        public long createdAtMillis() {
            return createdAtMillis;
        }

        public long finishedAtMillis() {
            return finishedAtMillis;
        }

        public int lastSeq() {
            return nextSeq - 1;
        }

        public int truncatedBefore() {
            return truncatedBefore;
        }

        public int bufferedEvents() {
            return events.size();
        }

        private void finish(String newStatus, long nowMillis) {
            this.status = newStatus;
            this.finishedAtMillis = nowMillis;
        }
    }

    private record StoredEvent(int seq, String data) {
    }

    /** Snapshot for the readiness/diagnostics endpoint. */
    public record Stats(
            int runs,
            int running,
            int finished,
            int bufferedEvents,
            int maxRuns,
            int maxEventsPerRun,
            long ttlSeconds,
            long evictedRuns,
            long truncatedEvents
    ) {
    }

    private final ConcurrentHashMap<String, RunRecord> runs = new ConcurrentHashMap<>();
    private final RunEventProperties properties;
    private final ScheduledExecutorService cleaner;
    private volatile long evictedRuns;
    private volatile long truncatedEvents;

    public RunEventStore(RunEventProperties properties) {
        this.properties = properties;
        long interval = Math.max(1_000L, properties.cleanupIntervalMs());
        this.cleaner = Executors.newSingleThreadScheduledExecutor(task -> {
            Thread thread = new Thread(task, "run-event-cleaner");
            thread.setDaemon(true);
            return thread;
        });
        this.cleaner.scheduleWithFixedDelay(
                this::evictQuietly,
                interval,
                interval,
                TimeUnit.MILLISECONDS
        );
    }

    public RunRecord getOrCreate(String runId, String userId, String conversationId) {
        return runs.computeIfAbsent(runId, ignored ->
                new RunRecord(runId, userId, conversationId, System.currentTimeMillis())
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
            return store(run, data);
        }
    }

    /** Appends under the record lock and returns the assigned sequence number. */
    private int store(RunRecord run, String data) {
        int seq = run.nextSeq++;
        run.events.add(new StoredEvent(seq, data));
        // Bound the per-run history. Dropping the oldest keeps the most recent context, and
        // `truncatedBefore` lets replay tell the client that it missed events instead of
        // silently skipping them.
        while (run.events.size() > properties.maxEventsPerRun()) {
            StoredEvent dropped = run.events.remove(0);
            run.truncatedBefore = dropped.seq();
            truncatedEvents++;
        }
        run.sink.tryEmitNext(toEvent(seq, data));
        return seq;
    }

    public boolean complete(String runId) {
        return finish(runId, "completed", "{\"type\":\"complete\"}");
    }

    public boolean fail(String runId, String message) {
        return finish(
                runId,
                "failed",
                "{\"type\":\"error\",\"message\":\"" + escapeJson(message) + "\"}"
        );
    }

    public boolean cancel(String runId) {
        return finish(
                runId,
                "cancelled",
                "{\"type\":\"error\",\"message\":\"本次生成已取消\",\"cancelled\":true}"
        );
    }

    private boolean finish(String runId, String status, String data) {
        RunRecord run = runs.get(runId);
        if (run == null || !"running".equals(run.status())) {
            return false;
        }
        synchronized (run) {
            if (!"running".equals(run.status())) {
                return false;
            }
            store(run, data);
            run.finish(status, System.currentTimeMillis());
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
        int truncatedBefore = run.truncatedBefore();
        if (afterSeq < truncatedBefore) {
            // The client's cursor points at events this instance no longer has. Say so
            // explicitly: a silent gap would look like the model skipped output.
            history.add(ServerSentEvent.<String>builder(
                            "{\"type\":\"truncated\",\"missingFrom\":" + (afterSeq + 1)
                                    + ",\"resumeFrom\":" + truncatedBefore + "}"
                    )
                    .id(String.valueOf(truncatedBefore))
                    .event("truncated")
                    .build());
        }
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

    /**
     * Drops finished runs past their TTL, then trims the oldest runs until the cap is met.
     *
     * <p>A running run is only evicted when nothing else can be, and that is logged: it means
     * the cap is smaller than the number of concurrent runs, so a client may lose its stream.</p>
     */
    public int evictExpired() {
        return evictExpired(System.currentTimeMillis());
    }

    int evictExpired(long nowMillis) {
        int removed = 0;
        long ttlMillis = Math.max(0L, properties.ttlSeconds()) * 1000L;

        for (Map.Entry<String, RunRecord> entry : runs.entrySet()) {
            RunRecord run = entry.getValue();
            long finishedAt = run.finishedAtMillis();
            if (finishedAt > 0 && nowMillis - finishedAt >= ttlMillis) {
                if (runs.remove(entry.getKey(), run)) {
                    removed++;
                }
            }
        }

        int overflow = runs.size() - Math.max(1, properties.maxRuns());
        if (overflow > 0) {
            List<RunRecord> oldestFirst = new ArrayList<>(runs.values());
            oldestFirst.sort(Comparator.comparingLong(RunRecord::createdAtMillis));
            for (RunRecord candidate : oldestFirst) {
                if (overflow <= 0) {
                    break;
                }
                // Prefer finished runs; only touch a running one if there is no alternative.
                if ("running".equals(candidate.status())) {
                    continue;
                }
                if (runs.remove(candidate.id(), candidate)) {
                    removed++;
                    overflow--;
                }
            }
            for (RunRecord candidate : oldestFirst) {
                if (overflow <= 0) {
                    break;
                }
                if (!"running".equals(candidate.status())) {
                    continue;
                }
                log.warn(
                        "Evicting running run {} to honour skychat.run-events.max-runs={}; "
                                + "raise the cap if this happens often.",
                        candidate.id(),
                        properties.maxRuns()
                );
                if (runs.remove(candidate.id(), candidate)) {
                    removed++;
                    overflow--;
                }
            }
        }

        evictedRuns += removed;
        return removed;
    }

    private void evictQuietly() {
        try {
            int removed = evictExpired();
            if (removed > 0) {
                log.debug("Evicted {} finished run(s) from the event buffer", removed);
            }
        } catch (RuntimeException error) {
            // A cleanup failure must never take the scheduler thread down.
            log.warn("Run-event cleanup failed: {}", error.toString());
        }
    }

    public Stats stats() {
        int running = 0;
        int buffered = 0;
        for (RunRecord run : runs.values()) {
            if ("running".equals(run.status())) {
                running++;
            }
            buffered += run.bufferedEvents();
        }
        return new Stats(
                runs.size(),
                running,
                runs.size() - running,
                buffered,
                properties.maxRuns(),
                properties.maxEventsPerRun(),
                properties.ttlSeconds(),
                evictedRuns,
                truncatedEvents
        );
    }

    @PreDestroy
    void shutdown() {
        cleaner.shutdownNow();
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
