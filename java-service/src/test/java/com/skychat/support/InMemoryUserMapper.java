package com.skychat.support;

import com.skychat.domain.UserAccount;
import com.skychat.mapper.UserMapper;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/**
 * In-memory {@link UserMapper} whose behaviour mirrors the SQL it replaces.
 *
 * <p>Written by hand rather than mocked: Mockito's inline mock maker cannot attach on this
 * JDK, and a fake that reproduces the real constraints (unique email, exact-token role
 * matching, disabled accounts excluded from the administrator count) lets the tests assert
 * on behaviour instead of on call recording. Every method documented here states which SQL
 * predicate it stands in for.</p>
 */
public class InMemoryUserMapper implements UserMapper {
    public final List<UserAccount> users = new ArrayList<>();

    @Override
    public UserAccount findByEmail(String email) {
        if (email == null) {
            return null;
        }
        return users.stream()
                .filter(user -> email.equalsIgnoreCase(user.getEmail()))
                .findFirst()
                .orElse(null);
    }

    @Override
    public UserAccount findById(String id) {
        return users.stream()
                .filter(user -> user.getId().equals(id))
                .findFirst()
                .orElse(null);
    }

    /** Mirrors the unique index on {@code email}: a duplicate is a constraint violation. */
    @Override
    public int insert(UserAccount user) {
        if (findByEmail(user.getEmail()) != null) {
            throw new IllegalStateException("duplicate key value violates unique constraint");
        }
        users.add(user);
        return 1;
    }

    /** Mirrors {@code ILIKE} on email/name plus the status predicate, newest first. */
    @Override
    public List<UserAccount> search(String query, String status, int limit, int offset) {
        List<UserAccount> matched = users.stream()
                .filter(user -> status == null || status.equals(user.getStatus()))
                .filter(user -> query == null || query.isBlank()
                        || user.getEmail().toLowerCase(Locale.ROOT)
                                .contains(query.toLowerCase(Locale.ROOT))
                        || user.getName().toLowerCase(Locale.ROOT)
                                .contains(query.toLowerCase(Locale.ROOT)))
                .sorted(Comparator.comparing(UserAccount::getCreatedAt,
                                Comparator.nullsLast(Comparator.reverseOrder()))
                        .thenComparing(UserAccount::getEmail))
                .toList();
        if (offset >= matched.size()) {
            return List.of();
        }
        return matched.subList(offset, Math.min(offset + limit, matched.size()));
    }

    @Override
    public int countSearch(String query, String status) {
        return search(query, status, Integer.MAX_VALUE, 0).size();
    }

    @Override
    public int updateRoles(String id, String roles) {
        UserAccount user = findById(id);
        if (user == null) {
            return 0;
        }
        user.setRoles(roles);
        return 1;
    }

    @Override
    public int updateStatus(String id, String status, LocalDateTime disabledAt) {
        UserAccount user = findById(id);
        if (user == null) {
            return 0;
        }
        user.setStatus(status);
        user.setDisabledAt(disabledAt);
        return 1;
    }

    @Override
    public int deleteById(String id) {
        return users.removeIf(user -> user.getId().equals(id)) ? 1 : 0;
    }

    /**
     * Mirrors the exact-token CSV match: {@code network_admin} alone is not an
     * administrator, and a disabled administrator is not counted.
     */
    @Override
    public int countAdministrators() {
        return (int) users.stream()
                .filter(user -> !user.isDisabled())
                .filter(user -> user.getRolesList().contains("admin")
                        || user.getRolesList().contains("user_admin"))
                .count();
    }

    /** Only the full {@code admin} role, matching the SQL token match. */
    @Override
    public int countFullAdministrators() {
        return (int) users.stream()
                .filter(user -> !user.isDisabled())
                .filter(user -> user.getRolesList().contains("admin"))
                .count();
    }
}
