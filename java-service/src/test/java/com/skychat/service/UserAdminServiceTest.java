package com.skychat.service;

import com.skychat.domain.UserAccount;
import com.skychat.support.InMemoryUserMapper;
import com.skychat.support.TestFixture;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Member management: authorisation, the delegation boundary, and the guards that stop an
 * administrator from removing their own access.
 */
class UserAdminServiceTest {

    private static final String STRONG_PASSWORD = "Sk7#trail-Blue42";
    private static final List<String> FULL_ADMIN = List.of("admin");
    private static final List<String> DELEGATED_ADMIN = List.of("user_admin");

    private InMemoryUserMapper userMapper;
    private UserAdminService service;

    @BeforeEach
    void setUp() {
        userMapper = new InMemoryUserMapper();
        service = new UserAdminService(
                userMapper,
                new AdminAuthorizer(),
                TestFixture.authService(userMapper)
        );
    }

    private UserAccount account(String id, String email, String... roles) {
        UserAccount user = new UserAccount();
        user.setId(id);
        user.setEmail(email);
        user.setName(email.split("@")[0]);
        user.setPasswordHash("hash");
        user.setRolesList(List.of(roles));
        user.setStatus(UserAccount.STATUS_ACTIVE);
        user.setCreatedAt(LocalDateTime.now());
        userMapper.insert(user);
        return user;
    }

    // ------------------------------------------------------------------ authorisation

    @Test
    @DisplayName("a non-admin is refused with FORBIDDEN_NOT_ADMIN")
    void nonAdminIsRefused() {
        assertThatThrownBy(() -> service.search(List.of("employee"), null, null, 0, 20))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AdminAuthorizer.FORBIDDEN_NOT_ADMIN));

        assertThatThrownBy(() -> service.createUser(
                List.of("employee"), "x@example.com", "X", List.of("employee"), STRONG_PASSWORD))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AdminAuthorizer.FORBIDDEN_NOT_ADMIN));
    }

    /**
     * {@code network_admin} contains the substring "admin" but administers nothing; the role
     * check must be token-exact or this would be a privilege-escalation bug.
     */
    @Test
    @DisplayName("network_admin is not an administrator")
    void networkAdminIsNotAnAdministrator() {
        assertThatThrownBy(() -> service.search(List.of("network_admin"), null, null, 0, 20))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AdminAuthorizer.FORBIDDEN_NOT_ADMIN));
    }

    @Test
    @DisplayName("a user_admin may onboard staff but may not grant administrator roles")
    void delegatedAdminCannotEscalate() {
        assertThat(service.createUser(
                DELEGATED_ADMIN, "new@example.com", "新同事", List.of("it_staff"), STRONG_PASSWORD))
                .isNotNull();

        assertThatThrownBy(() -> service.createUser(
                DELEGATED_ADMIN, "boss@example.com", "Boss", FULL_ADMIN, STRONG_PASSWORD))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AdminAuthorizer.FORBIDDEN_ROLE_ESCALATION));

        // Nor may it promote itself.
        UserAccount self = account("self", "self@example.com", "user_admin");
        assertThatThrownBy(() -> service.changeRoles(
                DELEGATED_ADMIN, self.getId(), List.of("admin", "user_admin")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(AdminAuthorizer.FORBIDDEN_ROLE_ESCALATION));
    }

    @Test
    @DisplayName("a full admin may grant administrator roles")
    void fullAdminMayGrant() {
        UserAccount created = service.createUser(
                FULL_ADMIN, "second@example.com", "Second", List.of("admin"), STRONG_PASSWORD);

        assertThat(created.getRolesList()).containsExactly("admin");
    }

    // ------------------------------------------------------------------ last admin guards

    @Test
    @DisplayName("the last administrator cannot be demoted")
    void lastAdminCannotBeDemoted() {
        UserAccount only = account("only", "only@example.com", "admin");

        assertThatThrownBy(() -> service.changeRoles(FULL_ADMIN, only.getId(), List.of("employee")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.LAST_ADMIN));
        assertThat(userMapper.findById(only.getId()).getRolesList()).containsExactly("admin");
    }

    @Test
    @DisplayName("the last administrator cannot be disabled")
    void lastAdminCannotBeDisabled() {
        UserAccount only = account("only", "only@example.com", "admin");

        assertThatThrownBy(() -> service.setEnabled(FULL_ADMIN, only.getId(), false, "someone-else"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.LAST_ADMIN));
        assertThat(userMapper.findById(only.getId()).getStatus())
                .isEqualTo(UserAccount.STATUS_ACTIVE);
    }

    /**
     * Regression test for a bug the live end-to-end run found: {@code user_admin} also counted
     * as an administrator, so the last full {@code admin} could be demoted while a
     * {@code user_admin} remained — leaving nobody who is allowed to grant administrative
     * roles.
     */
    @Test
    @DisplayName("the last full admin cannot be demoted just because a user_admin exists")
    void lastFullAdminProtectedEvenWithDelegatedAdmin() {
        UserAccount onlyFullAdmin = account("only", "only@example.com", "admin");
        account("delegate", "delegate@example.com", "user_admin");

        assertThatThrownBy(() -> service.changeRoles(
                FULL_ADMIN, onlyFullAdmin.getId(), List.of("employee")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.LAST_ADMIN));
        assertThat(userMapper.findById(onlyFullAdmin.getId()).getRolesList())
                .containsExactly("admin");
    }

    @Test
    @DisplayName("a user_admin alone is enough to demote another user_admin")
    void delegatedAdminsCanManageEachOther() {
        account("full", "full@example.com", "admin");
        account("first", "first@example.com", "user_admin");
        UserAccount second = account("second", "second@example.com", "user_admin");

        service.changeRoles(FULL_ADMIN, second.getId(), List.of("employee"));

        assertThat(userMapper.findById(second.getId()).getRolesList()).containsExactly("employee");
    }

    @Test
    @DisplayName("with two administrators one may be demoted or disabled")
    void demotionAllowedWhenAnotherAdminRemains() {        account("first", "first@example.com", "admin");
        UserAccount second = account("second", "second@example.com", "admin");

        service.changeRoles(FULL_ADMIN, second.getId(), List.of("employee"));
        assertThat(userMapper.findById(second.getId()).getRolesList()).containsExactly("employee");

        UserAccount third = account("third", "third@example.com", "admin");
        service.setEnabled(FULL_ADMIN, third.getId(), false, "someone-else");
        assertThat(userMapper.findById(third.getId()).getStatus())
                .isEqualTo(UserAccount.STATUS_DISABLED);
        assertThat(userMapper.findById(third.getId()).getDisabledAt()).isNotNull();
    }

    @Test
    @DisplayName("nobody can disable their own account")
    void cannotDisableSelf() {
        UserAccount me = account("me", "me@example.com", "admin");
        account("other", "other@example.com", "admin");

        assertThatThrownBy(() -> service.setEnabled(FULL_ADMIN, me.getId(), false, me.getId()))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.CANNOT_DISABLE_SELF));
    }

    @Test
    @DisplayName("a disabled administrator is not counted, so the last-admin guard still fires")
    void disabledAdministratorsDoNotCount() {
        UserAccount disabled = account("gone", "gone@example.com", "admin");
        userMapper.updateStatus(disabled.getId(), UserAccount.STATUS_DISABLED, LocalDateTime.now());
        UserAccount onlyActive = account("only", "only@example.com", "admin");

        assertThat(userMapper.countAdministrators()).isEqualTo(1);
        assertThatThrownBy(() -> service.changeRoles(FULL_ADMIN, onlyActive.getId(), List.of("employee")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.LAST_ADMIN));
    }

    // ------------------------------------------------------------------ listing and mutation

    @Test
    @DisplayName("search filters by status and query, and pages correctly")
    void searchFiltersAndPages() {
        account("a", "alice@example.com", "employee");
        account("b", "bob@example.com", "it_staff");
        UserAccount disabled = account("c", "carol@example.com", "employee");
        userMapper.updateStatus(disabled.getId(), UserAccount.STATUS_DISABLED, LocalDateTime.now());
        account("d", "dave@example.com", "employee");

        assertThat(service.search(FULL_ADMIN, null, null, 0, 20).total()).isEqualTo(4);
        assertThat(service.search(FULL_ADMIN, null, "disabled", 0, 20).items())
                .extracting(UserAccount::getEmail)
                .containsExactly("carol@example.com");
        assertThat(service.search(FULL_ADMIN, "bob", null, 0, 20).items())
                .extracting(UserAccount::getEmail)
                .containsExactly("bob@example.com");

        UserAdminService.Page firstPage = service.search(FULL_ADMIN, null, null, 0, 2);
        assertThat(firstPage.items()).hasSize(2);
        assertThat(firstPage.size()).isEqualTo(2);
        assertThat(firstPage.total()).isEqualTo(4);
        UserAdminService.Page secondPage = service.search(FULL_ADMIN, null, null, 1, 2);
        assertThat(secondPage.items()).hasSize(2);
        assertThat(secondPage.items()).doesNotContainAnyElementsOf(firstPage.items());
    }

    @Test
    @DisplayName("page size is clamped so a caller cannot ask for the whole table")
    void pageSizeIsClamped() {
        assertThat(service.search(FULL_ADMIN, null, null, 0, 10_000).size()).isEqualTo(100);
        assertThat(service.search(FULL_ADMIN, null, null, -3, 0).page()).isZero();
        assertThat(service.search(FULL_ADMIN, null, null, -3, 0).size()).isEqualTo(20);
    }

    @Test
    @DisplayName("changeRoles and setEnabled report an unknown user as USER_NOT_FOUND")
    void unknownUserIsNotFound() {
        assertThatThrownBy(() -> service.changeRoles(FULL_ADMIN, "nope", List.of("employee")))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.NOT_FOUND));
        assertThatThrownBy(() -> service.setEnabled(FULL_ADMIN, "nope", false, "actor"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.NOT_FOUND));
    }

    @Test
    @DisplayName("createUser rejects an unknown role and a weak password")
    void createUserValidatesInput() {
        assertThatThrownBy(() -> service.createUser(
                FULL_ADMIN, "x@example.com", "X", List.of("superuser"), STRONG_PASSWORD))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(UserAdminService.INVALID_ROLE));

        assertThatThrownBy(() -> service.createUser(
                FULL_ADMIN, "y@example.com", "Y", List.of("employee"), "short"))
                .isInstanceOf(AuthException.class)
                .satisfies(error -> assertThat(((AuthException) error).code())
                        .isEqualTo(PasswordPolicy.TOO_SHORT));
        assertThat(userMapper.users).isEmpty();
    }

    @Test
    @DisplayName("the assignable role list is what the console offers")
    void assignableRoles() {
        assertThat(service.assignableRoles())
                .contains("employee", "it_staff", "approver", "auditor", "user_admin", "admin");
    }
}
