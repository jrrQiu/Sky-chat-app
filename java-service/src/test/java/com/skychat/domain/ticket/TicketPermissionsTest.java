package com.skychat.domain.ticket;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Who may do what. These are security boundaries, not UI hints: the same predicates the
 * service uses to authorize are asserted here so a change of mind is visible.
 */
class TicketPermissionsTest {

    @Test
    @DisplayName("staff roles are agents")
    void staffRolesAreAgents() {
        for (String role : List.of("it_staff", "network_admin", "hr_staff", "approver",
                "finance_user", "user_admin", "admin")) {
            assertThat(TicketPermissions.isAgent(List.of(role)))
                    .as("%s should be an agent", role)
                    .isTrue();
        }
    }

    @Test
    @DisplayName("an employee is not an agent and not an auditor")
    void employeeIsARequester() {
        assertThat(TicketPermissions.isAgent(List.of("employee"))).isFalse();
        assertThat(TicketPermissions.isAuditorOnly(List.of("employee"))).isFalse();
        assertThat(TicketPermissions.canViewAll(List.of("employee"))).isFalse();
        assertThat(TicketPermissions.canUseInternalNotes(List.of("employee"))).isFalse();
        assertThat(TicketPermissions.canTransition(List.of("employee"))).isFalse();
    }

    @Test
    @DisplayName("an auditor reads everything and writes nothing")
    void auditorIsReadOnly() {
        List<String> auditor = List.of("auditor");

        assertThat(TicketPermissions.canViewAll(auditor)).isTrue();
        // Read-only oversight is meaningless if it can also mutate.
        assertThat(TicketPermissions.canTransition(auditor)).isFalse();
        assertThat(TicketPermissions.canAssign(auditor)).isFalse();
        assertThat(TicketPermissions.canEditFields(auditor)).isFalse();
        assertThat(TicketPermissions.canMergeOrSplit(auditor)).isFalse();
        assertThat(TicketPermissions.canLink(auditor)).isFalse();
        assertThat(TicketPermissions.canUseInternalNotes(auditor)).isFalse();
    }

    @Test
    @DisplayName("an auditor who is also staff gets the staff abilities")
    void auditorWithStaffRoleIsAnAgent() {
        List<String> both = List.of("auditor", "it_staff");

        assertThat(TicketPermissions.isAgent(both)).isTrue();
        assertThat(TicketPermissions.isAuditorOnly(both)).isFalse();
        assertThat(TicketPermissions.canTransition(both)).isTrue();
    }

    @Test
    @DisplayName("requester matching is exact and null-safe")
    void requesterMatching() {
        assertThat(TicketPermissions.isRequester("u1", "u1")).isTrue();
        assertThat(TicketPermissions.isRequester("u1", "u2")).isFalse();
        assertThat(TicketPermissions.isRequester(null, "u1")).isFalse();
        assertThat(TicketPermissions.isRequester("u1", null)).isFalse();
    }

    @Test
    @DisplayName("no roles at all grants nothing")
    void emptyRoles() {
        assertThat(TicketPermissions.isAgent(null)).isFalse();
        assertThat(TicketPermissions.isAgent(List.of())).isFalse();
        assertThat(TicketPermissions.canViewAll(null)).isFalse();
        assertThat(TicketPermissions.isAuditorOnly(List.of())).isFalse();
    }

    @Test
    @DisplayName("network_admin is an agent even though it is not a platform administrator")
    void networkAdminIsAnAgent() {
        // Deliberately distinct from UserRoles.canAdministerUsers: administering tickets is not
        // administering the platform.
        assertThat(TicketPermissions.isAgent(List.of("network_admin"))).isTrue();
        assertThat(com.skychat.domain.UserRoles.canAdministerUsers(List.of("network_admin")))
                .isFalse();
    }
}
