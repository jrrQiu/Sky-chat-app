package com.skychat.domain.ticket;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The lifecycle rules, including the ones that are easy to get subtly wrong: a reopen must not
 * look like a resolution, and a requester may only confirm or reject.
 */
class TicketStatusTest {

    @Test
    @DisplayName("a new ticket may go straight to any working state")
    void newTicketCanStartAnywhere() {
        assertThat(TicketStatus.NEW.allowedNext()).containsExactlyInAnyOrder(
                TicketStatus.OPEN,
                TicketStatus.IN_PROGRESS,
                TicketStatus.PENDING,
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED
        );
    }

    @Test
    @DisplayName("a resolved ticket may be closed or reopened, and nothing else")
    void resolvedIsNarrow() {
        assertThat(TicketStatus.RESOLVED.allowedNext())
                .containsExactlyInAnyOrder(TicketStatus.CLOSED, TicketStatus.OPEN);
    }

    @Test
    @DisplayName("a closed ticket can only be reopened")
    void closedOnlyReopens() {
        assertThat(TicketStatus.CLOSED.allowedNext()).containsExactly(TicketStatus.OPEN);
    }

    @Test
    @DisplayName("backwards moves between working states are allowed")
    void workingStatesCanMoveBackwards() {
        // A ticket parked on the customer is commonly moved back to in-progress, and a ticket
        // taken by the wrong person goes back to the queue.
        assertThat(TicketStatus.PENDING.canTransitionTo(TicketStatus.IN_PROGRESS)).isTrue();
        assertThat(TicketStatus.IN_PROGRESS.canTransitionTo(TicketStatus.OPEN)).isTrue();
        assertThat(TicketStatus.OPEN.canTransitionTo(TicketStatus.PENDING)).isTrue();
    }

    @Test
    @DisplayName("a terminal state is not reachable twice")
    void statusEqualsItselfIsNotATransition() {
        for (TicketStatus status : TicketStatus.values()) {
            assertThat(status.canTransitionTo(status))
                    .as("self transition for %s", status)
                    .isFalse();
        }
    }

    @Test
    @DisplayName("only resolved -> closed is exposed to a requester as confirmation")
    void requesterMayConfirmResolution() {
        assertThat(TicketStatus.RESOLVED.requesterMayTransitionTo(TicketStatus.CLOSED)).isTrue();
        assertThat(TicketStatus.RESOLVED.requesterMayTransitionTo(TicketStatus.OPEN)).isTrue();
        // A requester must not be able to resolve their own ticket or close it from new.
        assertThat(TicketStatus.NEW.requesterMayTransitionTo(TicketStatus.CLOSED)).isFalse();
        assertThat(TicketStatus.IN_PROGRESS.requesterMayTransitionTo(TicketStatus.RESOLVED))
                .isFalse();
        assertThat(TicketStatus.CLOSED.requesterMayTransitionTo(TicketStatus.OPEN)).isTrue();
    }

    @Test
    @DisplayName("parsing is case-insensitive and rejects unknown values")
    void parsing() {
        assertThat(TicketStatus.parse(" In_Progress ")).isEqualTo(TicketStatus.IN_PROGRESS);
        assertThat(TicketStatus.isKnown("closed")).isTrue();
        assertThat(TicketStatus.isKnown("done")).isFalse();
        assertThatThrownBy(() -> TicketStatus.parse("done"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TicketStatus.parse(null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("active covers exactly the states that are still work")
    void activeStates() {
        assertThat(TicketStatus.valuesAsStrings())
                .containsExactly("new", "open", "in_progress", "pending", "resolved", "closed");
        List<TicketStatus> active = java.util.Arrays.stream(TicketStatus.values())
                .filter(TicketStatus::isActive)
                .toList();
        assertThat(active).containsExactly(
                TicketStatus.NEW, TicketStatus.OPEN, TicketStatus.IN_PROGRESS,
                TicketStatus.PENDING
        );
    }

    @Test
    @DisplayName("every status has at least one legal move, so no ticket can be stranded")
    void noStatusIsADeadEnd() {
        for (TicketStatus status : TicketStatus.values()) {
            assertThat(status.allowedNext()).as("%s must be escapable", status).isNotEmpty();
        }
    }
}
