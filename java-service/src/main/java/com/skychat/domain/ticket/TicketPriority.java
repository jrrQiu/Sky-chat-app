package com.skychat.domain.ticket;

import java.util.List;
import java.util.Locale;

/**
 * Impact, urgency and the priority derived from them.
 *
 * <p>Priority is <strong>computed</strong> from the ITIL 3x3 matrix rather than taken from the
 * caller. The point of the matrix is that two agents looking at the same ticket reach the same
 * priority; letting a client post {@code priority} directly reintroduces the inconsistency the
 * matrix exists to remove. An explicit override is still accepted from an agent and is recorded
 * in the timeline as an override, so it is visible that the matrix was not followed.</p>
 */
public final class TicketPriority {
    public static final String CRITICAL = "critical";
    public static final String HIGH = "high";
    public static final String MEDIUM = "medium";
    public static final String LOW = "low";

    /** The three-point scale shared by impact and urgency. */
    public enum Level {
        HIGH("high", "高"),
        MEDIUM("medium", "中"),
        LOW("low", "低");

        private final String value;
        private final String label;

        Level(String value, String label) {
            this.value = value;
            this.label = label;
        }

        public String value() {
            return value;
        }

        public String label() {
            return label;
        }

        public static Level parse(String raw) {
            if (raw == null || raw.isBlank()) {
                throw new IllegalArgumentException("level is required");
            }
            String normalized = raw.trim().toLowerCase(Locale.ROOT);
            for (Level level : values()) {
                if (level.value.equals(normalized)) {
                    return level;
                }
            }
            throw new IllegalArgumentException("unknown level: " + raw);
        }

        public static List<String> valuesAsStrings() {
            return java.util.Arrays.stream(values()).map(Level::value).toList();
        }
    }

    private static final List<String> VALUES = List.of(CRITICAL, HIGH, MEDIUM, LOW);

    private TicketPriority() {
    }

    public static List<String> valuesAsStrings() {
        return VALUES;
    }

    public static boolean isKnown(String raw) {
        return raw != null && VALUES.contains(raw.trim().toLowerCase(Locale.ROOT));
    }

    public static String normalize(String raw) {
        if (!isKnown(raw)) {
            throw new IllegalArgumentException("unknown priority: " + raw);
        }
        return raw.trim().toLowerCase(Locale.ROOT);
    }

    /**
     * The ITIL impact x urgency matrix.
     *
     * <pre>
     *            urgency high   urgency medium  urgency low
     * impact high    critical   high            medium
     * impact med     high       medium          low
     * impact low     medium     low             low
     * </pre>
     */
    public static String derive(Level impact, Level urgency) {
        if (impact == Level.HIGH) {
            return switch (urgency) {
                case HIGH -> CRITICAL;
                case MEDIUM -> HIGH;
                case LOW -> MEDIUM;
            };
        }
        if (impact == Level.MEDIUM) {
            return switch (urgency) {
                case HIGH -> HIGH;
                case MEDIUM -> MEDIUM;
                case LOW -> LOW;
            };
        }
        return switch (urgency) {
            case HIGH -> MEDIUM;
            case MEDIUM, LOW -> LOW;
        };
    }

    public static String derive(String impact, String urgency) {
        return derive(Level.parse(impact), Level.parse(urgency));
    }
}
