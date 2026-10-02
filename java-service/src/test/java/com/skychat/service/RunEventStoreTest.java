package com.skychat.service;

import com.skychat.config.RunEventProperties;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.codec.ServerSentEvent;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Bounded run-event buffering.
 *
 * <p>The store used to keep every run and every event for the lifetime of the process. These
 * tests pin the three bounds (events per run, TTL, retained runs) and the one behaviour that
 * must not regress while bounding memory: a client whose cursor points at dropped events is
 * told so instead of silently receiving a gap.</p>
 */
class RunEventStoreTest {

    private RunEventStore store;

    private RunEventStore newStore(int maxRuns, int ttlSeconds, int maxEventsPerRun) {
        return new RunEventStore(new RunEventProperties(maxRuns, ttlSeconds, maxEventsPerRun, 3_600_000));
    }

    @BeforeEach
    void setUp() {
        store = newStore(10, 60, 5);
    }

    @AfterEach
    void tearDown() {
        store.shutdown();
    }

    @Test
    @DisplayName("a run keeps a bounded number of events and reports what it dropped")
    void perRunCapDropsOldestAndReportsTruncation() {
        store.getOrCreate("run-1", "user-1", "conv-1");
        for (int i = 0; i < 8; i++) {
            store.append("run-1", "{\"type\":\"delta\",\"n\":" + i + "}");
        }

        RunEventStore.RunRecord run = store.get("run-1");
        assertThat(run.bufferedEvents()).isEqualTo(5);
        // Eight events were written, so the last sequence number is 8 even though only five
        // remain. Deriving the sequence from the buffer size would repeat numbers here.
        assertThat(run.lastSeq()).isEqualTo(8);
        assertThat(run.truncatedBefore()).isEqualTo(3);
        assertThat(store.stats().truncatedEvents()).isEqualTo(3);
    }

    @Test
    @DisplayName("a cursor below the truncation point gets an explicit truncated event")
    void replaySignalsTruncation() throws InterruptedException {
        store.getOrCreate("run-2", "user-1", "conv-1");
        for (int i = 0; i < 8; i++) {
            store.append("run-2", "{\"n\":" + i + "}");
        }
        // Completion appends its own event, so the cap then drops sequences 1..4.
        store.complete("run-2");

        RunEventStore.RunRecord run = store.get("run-2");
        assertThat(run.lastSeq()).isEqualTo(9);
        assertThat(run.truncatedBefore()).isEqualTo(4);

        List<ServerSentEvent<String>> replayed = store.replay("run-2", 0).collectList().block();
        assertThat(replayed).isNotNull();
        assertThat(replayed.get(0).event()).isEqualTo("truncated");
        assertThat(replayed.get(0).data()).contains("\"resumeFrom\":4");

        // A cursor inside the retained window must not produce the marker: nothing was missed.
        List<ServerSentEvent<String>> fromFive = store.replay("run-2", 5).collectList().block();
        assertThat(fromFive).isNotNull();
        assertThat(fromFive).noneMatch(event -> "truncated".equals(event.event()));
        assertThat(fromFive).extracting(ServerSentEvent::id)
                .containsExactly("6", "7", "8", "9");
    }

    @Test
    @DisplayName("finished runs are dropped once their TTL passes")
    void ttlEvictsFinishedRuns() {
        store.getOrCreate("run-3", "user-1", "conv-1");
        store.append("run-3", "{\"n\":1}");
        store.complete("run-3");

        RunEventStore.RunRecord finished = store.get("run-3");
        long finishedAt = finished.finishedAtMillis();
        assertThat(finishedAt).isGreaterThan(0);

        // Just inside the TTL.
        store.evictExpired(finishedAt + 59_000);
        assertThat(store.get("run-3")).isNotNull();

        // Past it.
        store.evictExpired(finishedAt + 61_000);
        assertThat(store.get("run-3")).isNull();
        assertThat(store.stats().evictedRuns()).isEqualTo(1);
    }

    @Test
    @DisplayName("a running run is not evicted by the TTL pass")
    void ttlDoesNotTouchRunningRuns() {
        store.getOrCreate("run-4", "user-1", "conv-1");
        store.append("run-4", "{\"n\":1}");

        store.evictExpired(System.currentTimeMillis() + 86_400_000L);

        assertThat(store.get("run-4")).isNotNull();
    }

    @Test
    @DisplayName("exceeding max-runs evicts finished runs before running ones")
    void runCapPrefersFinishedRuns() {
        store.shutdown();
        store = newStore(2, 3600, 100);

        // Two long-lived running runs, then a third finished one.
        store.getOrCreate("running-a", "u", "c");
        store.getOrCreate("running-b", "u", "c");
        store.getOrCreate("finished-c", "u", "c");
        store.complete("finished-c");

        store.evictExpired();

        // The finished run is the cheapest to drop, so both running runs survive.
        assertThat(store.get("finished-c")).isNull();
        assertThat(store.get("running-a")).isNotNull();
        assertThat(store.get("running-b")).isNotNull();
        assertThat(store.stats().runs()).isEqualTo(2);
    }

    @Test
    @DisplayName("when only running runs remain the cap still wins, because memory is the hard limit")
    void runCapEvictsRunningRunsAsALastResort() throws InterruptedException {
        store.shutdown();
        store = newStore(1, 3600, 100);

        store.getOrCreate("oldest", "u", "c");
        // Creation timestamps are the ordering key, and two runs made in the same millisecond
        // would tie. Separate them so the assertion is about eviction order, not clock resolution.
        Thread.sleep(5);
        store.getOrCreate("newer", "u", "c");

        store.evictExpired();

        assertThat(store.stats().runs()).isEqualTo(1);
        assertThat(store.get("oldest")).isNull();
        assertThat(store.get("newer")).isNotNull();
    }

    @Test
    @DisplayName("appending to an unknown or finished run is refused")
    void appendIsRefusedAfterTheRunEnds() {
        assertThat(store.append("missing", "{}")).isEqualTo(-1);

        store.getOrCreate("run-5", "u", "c");
        assertThat(store.append("run-5", "{\"n\":1}")).isEqualTo(1);
        store.complete("run-5");

        assertThat(store.append("run-5", "{\"n\":2}")).isEqualTo(-1);
        // Completion itself is idempotent-safe: a second call does nothing.
        assertThat(store.complete("run-5")).isFalse();
    }

    @Test
    @DisplayName("a stream that has not finished stays open after replay")
    void replayKeepsRunningStreamsOpen() {
        store.getOrCreate("run-6", "u", "c");
        store.append("run-6", "{\"n\":1}");

        List<ServerSentEvent<String>> first = store.replay("run-6", 0)
                .take(1)
                .collectList()
                .block();
        assertThat(first).isNotNull();
        assertThat(first).hasSize(1);

        // Still running, so a later append reaches the live sink.
        store.append("run-6", "{\"n\":2}");
        assertThat(store.get("run-6").lastSeq()).isEqualTo(2);
    }

    @Test
    @DisplayName("stats report the configured bounds so the caps are visible to operators")
    void statsExposeTheBounds() {
        store.getOrCreate("run-7", "u", "c");
        store.append("run-7", "{}");
        store.complete("run-7");

        RunEventStore.Stats stats = store.stats();
        assertThat(stats.runs()).isEqualTo(1);
        assertThat(stats.running()).isZero();
        assertThat(stats.finished()).isEqualTo(1);
        assertThat(stats.maxRuns()).isEqualTo(10);
        assertThat(stats.maxEventsPerRun()).isEqualTo(5);
        assertThat(stats.ttlSeconds()).isEqualTo(60);
    }
}
