package com.skychat.domain.ticket;

import java.util.List;
import java.util.Locale;

/**
 * The ITSM record types.
 *
 * <p>Kept as a closed set: a free-text type makes routing, reporting and the priority matrix
 * unenforceable. {@code incident} is something broken, {@code request} is something wanted,
 * {@code problem} is the underlying cause of incidents, {@code change} is planned work.</p>
 */
public enum TicketType {
    INCIDENT("incident", "故障"),
    REQUEST("request", "服务请求"),
    PROBLEM("problem", "问题"),
    CHANGE("change", "变更");

    private final String value;
    private final String label;

    TicketType(String value, String label) {
        this.value = value;
        this.label = label;
    }

    public String value() {
        return value;
    }

    public String label() {
        return label;
    }

    public static TicketType parse(String raw) {
        if (raw == null || raw.isBlank()) {
            throw new IllegalArgumentException("type is required");
        }
        String normalized = raw.trim().toLowerCase(Locale.ROOT);
        for (TicketType type : values()) {
            if (type.value.equals(normalized)) {
                return type;
            }
        }
        throw new IllegalArgumentException("unknown ticket type: " + raw);
    }

    public static List<String> valuesAsStrings() {
        return java.util.Arrays.stream(values()).map(TicketType::value).toList();
    }
}
