package com.skychat.domain.ticket;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The ticket lifecycle, and the only authority on which transitions are legal.
 *
 * <p>Transitions are validated on the server for every request rather than trusted from the
 * client, because the status drives SLA clocks, reporting and reopen semantics. A free-text
 * status field would let a caller set {@code closed} directly and silently skip resolution.</p>
 */
public enum TicketStatus {
    NEW("new", "新建"),
    OPEN("open", "待处理"),
    IN_PROGRESS("in_progress", "处理中"),
    PENDING("pending", "挂起"),
    RESOLVED("resolved", "已解决"),
    CLOSED("closed", "已关闭");

    private final String value;
    private final String label;

    TicketStatus(String value, String label) {
        this.value = value;
        this.label = label;
    }

    public String value() {
        return value;
    }

    public String label() {
        return label;
    }

    public static TicketStatus parse(String raw) {
        if (raw == null || raw.isBlank()) {
            throw new IllegalArgumentException("status is required");
        }
        String normalized = raw.trim().toLowerCase(Locale.ROOT);
        for (TicketStatus status : values()) {
            if (status.value.equals(normalized)) {
                return status;
            }
        }
        throw new IllegalArgumentException("unknown status: " + raw);
    }

    public static boolean isKnown(String raw) {
        try {
            parse(raw);
            return true;
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    public static List<String> valuesAsStrings() {
        return java.util.Arrays.stream(values()).map(TicketStatus::value).toList();
    }

    /**
     * Legal next states.
     *
     * <p>Every state may be resolved or closed except that a reopen only ever goes back to
     * {@code open}, and {@code new} may go straight to any working state so a ticket created
     * by an automated channel is not forced through a manual acknowledgement step.</p>
     */
    public Set<TicketStatus> allowedNext() {
        return switch (this) {
            case NEW -> ordered(OPEN, IN_PROGRESS, PENDING, RESOLVED, CLOSED);
            case OPEN -> ordered(IN_PROGRESS, PENDING, RESOLVED, CLOSED);
            case IN_PROGRESS -> ordered(OPEN, PENDING, RESOLVED, CLOSED);
            case PENDING -> ordered(OPEN, IN_PROGRESS, RESOLVED, CLOSED);
            case RESOLVED -> ordered(CLOSED, OPEN);
            case CLOSED -> ordered(OPEN);
        };
    }

    private static Set<TicketStatus> ordered(TicketStatus... statuses) {
        return new LinkedHashSet<>(List.of(statuses));
    }

    public boolean canTransitionTo(TicketStatus next) {
        return next != null && allowedNext().contains(next);
    }

    public boolean isResolvedOrClosed() {
        return this == RESOLVED || this == CLOSED;
    }

    /** Whether the ticket still counts as open work (used by the "active" list filters). */
    public boolean isActive() {
        return !isResolvedOrClosed();
    }

    /**
     * Transitions a requester may perform on their own ticket: confirm the fix, or reject it
     * and send the ticket back. Everything else belongs to an agent.
     */
    public boolean requesterMayTransitionTo(TicketStatus next) {
        if (this == RESOLVED && next == CLOSED) {
            return true;
        }
        return isResolvedOrClosed() && next == OPEN;
    }
}
