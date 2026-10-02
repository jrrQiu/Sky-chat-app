package com.skychat.domain.ticket;

import com.skychat.domain.ticket.TicketPriority.Level;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The impact x urgency matrix. Every cell is asserted, because the whole value of a matrix is
 * that two agents reach the same answer — a wrong cell silently mis-prioritises work.
 */
class TicketPriorityTest {

    @Test
    @DisplayName("the full 3x3 matrix")
    void matrix() {
        // impact high
        assertThat(TicketPriority.derive(Level.HIGH, Level.HIGH)).isEqualTo(TicketPriority.CRITICAL);
        assertThat(TicketPriority.derive(Level.HIGH, Level.MEDIUM)).isEqualTo(TicketPriority.HIGH);
        assertThat(TicketPriority.derive(Level.HIGH, Level.LOW)).isEqualTo(TicketPriority.MEDIUM);
        // impact medium
        assertThat(TicketPriority.derive(Level.MEDIUM, Level.HIGH)).isEqualTo(TicketPriority.HIGH);
        assertThat(TicketPriority.derive(Level.MEDIUM, Level.MEDIUM)).isEqualTo(TicketPriority.MEDIUM);
        assertThat(TicketPriority.derive(Level.MEDIUM, Level.LOW)).isEqualTo(TicketPriority.LOW);
        // impact low
        assertThat(TicketPriority.derive(Level.LOW, Level.HIGH)).isEqualTo(TicketPriority.MEDIUM);
        assertThat(TicketPriority.derive(Level.LOW, Level.MEDIUM)).isEqualTo(TicketPriority.LOW);
        assertThat(TicketPriority.derive(Level.LOW, Level.LOW)).isEqualTo(TicketPriority.LOW);
    }

    @Test
    @DisplayName("critical requires both axes to be high")
    void criticalIsReserved() {
        for (Level impact : Level.values()) {
            for (Level urgency : Level.values()) {
                boolean both = impact == Level.HIGH && urgency == Level.HIGH;
                assertThat(TicketPriority.CRITICAL.equals(
                        TicketPriority.derive(impact, urgency)))
                        .as("impact=%s urgency=%s", impact, urgency)
                        .isEqualTo(both);
            }
        }
    }

    @Test
    @DisplayName("raising either axis never lowers the priority")
    void monotonic() {
        // A matrix that could lower priority when urgency rises would be indefensible.
        Level[] levels = Level.values();
        int[][] rank = new int[levels.length][levels.length];
        for (int i = 0; i < levels.length; i++) {
            for (int j = 0; j < levels.length; j++) {
                rank[i][j] = rankOf(TicketPriority.derive(levels[i], levels[j]));
            }
        }
        // Level order is HIGH, MEDIUM, LOW, so improving an axis means moving to a lower index.
        for (int i = 0; i < levels.length; i++) {
            for (int j = 1; j < levels.length; j++) {
                assertThat(rank[i][j - 1])
                        .as("raising urgency at impact=%s must not lower priority", levels[i])
                        .isGreaterThanOrEqualTo(rank[i][j]);
            }
        }
        for (int j = 0; j < levels.length; j++) {
            for (int i = 1; i < levels.length; i++) {
                assertThat(rank[i - 1][j])
                        .as("raising impact at urgency=%s must not lower priority", levels[j])
                        .isGreaterThanOrEqualTo(rank[i][j]);
            }
        }
    }

    private int rankOf(String priority) {
        return switch (priority) {
            case TicketPriority.CRITICAL -> 3;
            case TicketPriority.HIGH -> 2;
            case TicketPriority.MEDIUM -> 1;
            default -> 0;
        };
    }

    @Test
    @DisplayName("string derivation matches the enum form")
    void deriveFromStrings() {
        assertThat(TicketPriority.derive("high", "high")).isEqualTo(TicketPriority.CRITICAL);
        assertThat(TicketPriority.derive(" low ", "HIGH")).isEqualTo(TicketPriority.MEDIUM);
    }

    @Test
    @DisplayName("unknown levels and priorities are refused")
    void validation() {
        assertThatThrownBy(() -> TicketPriority.Level.parse("urgent"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(TicketPriority.isKnown("critical")).isTrue();
        assertThat(TicketPriority.isKnown("blocker")).isFalse();
        assertThatThrownBy(() -> TicketPriority.normalize("blocker"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("the four priorities are exposed in severity order for the UI")
    void order() {
        assertThat(TicketPriority.valuesAsStrings())
                .containsExactly("critical", "high", "medium", "low");
    }
}
