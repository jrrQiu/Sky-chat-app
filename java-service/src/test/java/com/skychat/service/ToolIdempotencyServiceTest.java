package com.skychat.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.skychat.support.InMemoryAgentToolIdempotencyMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.LocalDateTime;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Cross-service idempotency (T3): a repeated delivery of the same write must return the first
 * answer instead of repeating the effect.
 *
 * <p>What this can and cannot promise is asserted explicitly, because the difference matters:
 * a *duplicate* delivery is deduplicated, a *concurrent* one is not (there is no reservation).
 * The agent only retries when the connection was never established, so the gap is bounded, but
 * a test that pretended otherwise would be the kind of reassurance this project keeps refusing
 * to give.</p>
 */
class ToolIdempotencyServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private InMemoryAgentToolIdempotencyMapper mapper;
    private ToolIdempotencyService service;

    @BeforeEach
    void setUp() {
        mapper = new InMemoryAgentToolIdempotencyMapper();
        service = new ToolIdempotencyService(mapper, objectMapper);
        ReflectionTestUtils.setField(service, "retentionHours", 24);
    }

    private Map<String, Object> created(int number) {
        return Map.of(
                "success", true,
                "text", "已创建工单 #" + number,
                "data", Map.of("number", number, "status", "new")
        );
    }

    @Test
    void only_write_tools_are_deduplicated() {
        assertThat(service.deduplicates("ticket_create")).isTrue();
        assertThat(service.deduplicates("ticket_comment")).isTrue();
        assertThat(service.deduplicates("ticket_merge")).isTrue();
        // A repeated read is *supposed* to return current data; serving a cached page would be
        // a different and worse bug.
        assertThat(service.deduplicates("ticket_list")).isFalse();
        assertThat(service.deduplicates("ticket_get")).isFalse();
    }

    @Test
    void a_replayed_key_returns_the_original_result() {
        service.record("ticket_create", "idem_1", created(7), "user-1");

        Map<String, Object> replay = service.replay("ticket_create", "idem_1");

        assertThat(replay).isNotNull();
        assertThat(replay.get("deduplicated")).isEqualTo(true);
        assertThat(replay.get("text")).isEqualTo("已创建工单 #7");
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) replay.get("data");
        assertThat(data.get("number")).isEqualTo(7);
    }

    @Test
    void a_read_is_never_replayed() {
        service.record("ticket_list", "idem_read", Map.of("success", true, "text", "共 1 张"), "user-1");
        assertThat(service.replay("ticket_list", "idem_read")).isNull();
    }

    @Test
    void a_failure_is_not_recorded() {
        service.record(
                "ticket_create",
                "idem_fail",
                Map.of("success", false, "text", "工单服务暂时不可用"),
                "user-1"
        );
        assertThat(service.replay("ticket_create", "idem_fail")).isNull();
        assertThat(mapper.insertAttempts.get()).isZero();
    }

    @Test
    void a_blank_key_disables_deduplication() {
        service.record("ticket_create", "", created(7), "user-1");
        service.record("ticket_create", null, created(7), "user-1");
        assertThat(mapper.insertAttempts.get()).isZero();
        assertThat(service.replay("ticket_create", "")).isNull();
    }

    @Test
    void two_different_tools_may_share_a_key() {
        // A caller that derives the key from the turn id will hand the same value to whatever
        // the model chose; those are different operations and must not collide.
        service.record("ticket_create", "idem_turn", created(7), "user-1");
        service.record("ticket_comment", "idem_turn", Map.of("success", true, "text", "已回复"), "user-1");

        assertThat(service.replay("ticket_create", "idem_turn").get("text"))
                .isEqualTo("已创建工单 #7");
        assertThat(service.replay("ticket_comment", "idem_turn").get("text"))
                .isEqualTo("已回复");
    }

    @Test
    void the_first_result_wins_when_two_deliveries_race() {
        service.record("ticket_create", "idem_race", created(7), "user-1");
        service.record("ticket_create", "idem_race", created(8), "user-1");

        assertThat(mapper.conflicts.get()).isEqualTo(1);
        assertThat(service.replay("ticket_create", "idem_race").get("text"))
                .isEqualTo("已创建工单 #7");
    }

    @Test
    void an_unreadable_stored_response_does_not_block_the_call() {
        // Note the deliberate bad value: a corrupt row must degrade to "execute normally",
        // never to "refuse the write".
        mapper.insertIfAbsent("ticket_create", "idem_bad", "not json at all", "user-1");
        assertThat(service.replay("ticket_create", "idem_bad")).isNull();
    }

    @Test
    void a_lookup_failure_does_not_turn_a_write_into_a_refusal() {
        var broken = new InMemoryAgentToolIdempotencyMapper() {
            @Override
            public String findResponse(String toolName, String idemKey) {
                throw new IllegalStateException("database is down");
            }
        };
        var degraded = new ToolIdempotencyService(broken, objectMapper);
        assertThat(degraded.replay("ticket_create", "idem_x")).isNull();
    }

    @Test
    void a_record_failure_does_not_throw_into_the_request_path() {
        var broken = new InMemoryAgentToolIdempotencyMapper() {
            @Override
            public int insertIfAbsent(String toolName, String idemKey, String response, String actorId) {
                throw new IllegalStateException("database is down");
            }
        };
        var degraded = new ToolIdempotencyService(broken, objectMapper);
        // The write already happened; reporting a failure here would report a failure for
        // something that succeeded.
        degraded.record("ticket_create", "idem_y", created(9), "user-1");
    }

    @Test
    void retention_sweeps_keys_that_can_no_longer_be_replayed() {
        service.record("ticket_create", "idem_old", created(7), "user-1");
        mapper.backdate("ticket_create", "idem_old", LocalDateTime.now().minusHours(48));

        service.sweepExpired();

        assertThat(mapper.count()).isZero();
        assertThat(service.replay("ticket_create", "idem_old")).isNull();
    }

    @Test
    void a_recent_key_survives_the_sweep() {
        service.record("ticket_create", "idem_new", created(7), "user-1");
        service.sweepExpired();
        assertThat(mapper.count()).isEqualTo(1);
    }

    @Test
    void status_reports_whether_deduplication_is_really_active() {
        service.record("ticket_create", "idem_1", created(7), "user-1");
        Map<String, Object> status = service.status();

        assertThat(status.get("active")).isEqualTo(true);
        assertThat(status.get("recordedKeys")).isEqualTo(1);
        assertThat(String.valueOf(status.get("deduplicatedTools")))
                .contains("ticket_create", "ticket_comment", "ticket_merge");
    }

    @Test
    void status_says_inactive_when_the_table_is_unreachable() {
        var broken = new InMemoryAgentToolIdempotencyMapper() {
            @Override
            public int count() {
                throw new IllegalStateException("database is down");
            }
        };
        Map<String, Object> status = new ToolIdempotencyService(broken, objectMapper).status();
        // Claiming "active" while the table is unreachable is exactly the kind of reassurance
        // this project keeps refusing to give.
        assertThat(status.get("active")).isEqualTo(false);
        assertThat(status.get("reason")).isEqualTo("IllegalStateException");
    }
}
