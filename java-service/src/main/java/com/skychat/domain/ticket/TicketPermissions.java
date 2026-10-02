package com.skychat.domain.ticket;

import com.skychat.domain.UserRoles;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Who may do what to a ticket.
 *
 * <p>Three audiences, and the distinction is a security boundary rather than a UI hint:</p>
 * <ul>
 *   <li><strong>requester</strong> — sees only their own tickets, may add public replies, and
 *       may confirm or reject a resolution. Never sees internal notes.</li>
 *   <li><strong>agent</strong> — staff roles; sees every ticket, drives the lifecycle, assigns,
 *       merges and writes internal notes.</li>
 *   <li><strong>auditor</strong> — reads everything, writes nothing. Read-only oversight is
 *       worthless if it can also mutate.</li>
 * </ul>
 *
 * <p>An invisible ticket is reported as not-found rather than forbidden: a 403 would confirm
 * that the id exists.</p>
 */
public final class TicketPermissions {
    /**
     * Staff roles that count as a ticket agent. {@code auditor} is deliberately absent — it is
     * a read-only role — and {@code employee} is the default requester role.
     */
    public static final Set<String> AGENT_ROLES = new LinkedHashSet<>(List.of(
            "it_staff",
            "network_admin",
            "hr_staff",
            "approver",
            "finance_user",
            UserRoles.USER_ADMIN,
            UserRoles.ADMIN
    ));

    private TicketPermissions() {
    }

    public static List<String> agentRoles() {
        return List.copyOf(AGENT_ROLES);
    }

    public static boolean isAgent(List<String> roles) {
        if (roles == null) {
            return false;
        }
        return roles.stream().anyMatch(AGENT_ROLES::contains);
    }

    /** Read-only oversight: may see everything, may change nothing. */
    public static boolean isAuditorOnly(List<String> roles) {
        if (roles == null || roles.isEmpty()) {
            return false;
        }
        return roles.contains("auditor") && !isAgent(roles);
    }

    /** Whoever may look at any ticket rather than only their own. */
    public static boolean canViewAll(List<String> roles) {
        return isAgent(roles) || isAuditorOnly(roles);
    }

    public static boolean canTransition(List<String> roles) {
        return isAgent(roles);
    }

    public static boolean canAssign(List<String> roles) {
        return isAgent(roles);
    }

    public static boolean canEditFields(List<String> roles) {
        return isAgent(roles);
    }

    /** Internal notes are agent-only; a requester must never receive one. */
    public static boolean canUseInternalNotes(List<String> roles) {
        return isAgent(roles);
    }

    public static boolean canMergeOrSplit(List<String> roles) {
        return isAgent(roles);
    }

    public static boolean canLink(List<String> roles) {
        return isAgent(roles);
    }

    /** The requester of the ticket, and nobody else, may act on it as its requester. */
    public static boolean isRequester(String requesterId, String userId) {
        return requesterId != null && requesterId.equals(userId);
    }
}
