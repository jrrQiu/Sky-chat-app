package com.skychat.domain.ticket;

import java.util.List;
import java.util.Locale;

/**
 * Relations between tickets.
 *
 * <p>Only the declared direction is stored on a link row. The reverse view is derived, so
 * "A blocks B" shows on B as {@code blocked_by} and the two sides can never disagree.</p>
 *
 * <p>For {@code blocks}/{@code blocked_by} the inverse is a true mirror. For
 * {@code related}, {@code duplicates} and {@code caused_by} no distinct reverse term exists in
 * the vocabulary, so the reader keeps the same type and relies on {@code direction} to phrase
 * it ("X 声明与本单重复"). Inventing terms like {@code duplicated_by} would have meant the API
 * exposing link types the writer can never set.</p>
 */
public enum TicketLinkType {
    RELATED("related", "相关"),
    DUPLICATES("duplicates", "重复"),
    BLOCKS("blocks", "阻塞"),
    BLOCKED_BY("blocked_by", "被阻塞"),
    CAUSED_BY("caused_by", "由…引起");

    private final String value;
    private final String label;

    TicketLinkType(String value, String label) {
        this.value = value;
        this.label = label;
    }

    public String value() {
        return value;
    }

    public String label() {
        return label;
    }

    public static TicketLinkType parse(String raw) {
        if (raw == null || raw.isBlank()) {
            throw new IllegalArgumentException("link type is required");
        }
        String normalized = raw.trim().toLowerCase(Locale.ROOT);
        for (TicketLinkType type : values()) {
            if (type.value.equals(normalized)) {
                return type;
            }
        }
        throw new IllegalArgumentException("unknown link type: " + raw);
    }

    public static List<String> valuesAsStrings() {
        return java.util.Arrays.stream(values()).map(TicketLinkType::value).toList();
    }

    /** How the other end of the link reads it. */
    public TicketLinkType inverse() {
        return switch (this) {
            case BLOCKS -> BLOCKED_BY;
            case BLOCKED_BY -> BLOCKS;
            case RELATED, DUPLICATES, CAUSED_BY -> this;
        };
    }
}
