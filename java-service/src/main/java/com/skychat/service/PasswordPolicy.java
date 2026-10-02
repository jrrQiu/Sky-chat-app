package com.skychat.service;

import org.springframework.stereotype.Component;

import java.util.Locale;
import java.util.Set;

/**
 * Password strength rules for self-registration and password changes.
 *
 * <p>Three machine-readable failure codes are returned instead of prose so the front end can
 * localise the message and the tests can assert on a stable contract:</p>
 * <ul>
 *   <li>{@code PASSWORD_TOO_SHORT} - fewer than 12 characters</li>
 *   <li>{@code PASSWORD_TOO_WEAK} - fewer than 3 of the 4 character classes</li>
 *   <li>{@code PASSWORD_TOO_COMMON} - present in the built-in deny-list</li>
 * </ul>
 */
@Component
public class PasswordPolicy {
    public static final int MIN_LENGTH = 12;
    public static final int MIN_CLASSES = 3;

    public static final String TOO_SHORT = "PASSWORD_TOO_SHORT";
    public static final String TOO_WEAK = "PASSWORD_TOO_WEAK";
    public static final String TOO_COMMON = "PASSWORD_TOO_COMMON";

    /**
     * A deliberately small built-in deny-list of passwords and their most common variants.
     *
     * <p>Every entry is at least {@link #MIN_LENGTH} characters on purpose: a shorter entry
     * could never be reached, because the length rule rejects it first, and a dead entry in a
     * security list is worse than no entry at all.</p>
     *
     * <p>It is not a substitute for a breached-password corpus; it blocks the handful of
     * passwords that show up in every credential-stuffing run and costs nothing to check.
     * Comparison is case-insensitive.</p>
     */
    private static final Set<String> DENY_LIST = Set.of(
            "password",
            "password1",
            "password12",
            "password123",
            "password1234",
            "password12345",
            "passw0rd",
            "passw0rd!1234",
            "p@ssw0rd",
            "p@ssw0rd1234",
            "123456",
            "1234567",
            "12345678",
            "123456789",
            "1234567890",
            "12345678901",
            "123456789012",
            "qwerty",
            "qwerty123",
            "qwertyuiop",
            "qwertyuiop12",
            "qwerty123456",
            "abc123",
            "abc123456789",
            "letmein",
            "letmein12345",
            "welcome",
            "welcome1",
            "welcome12345",
            "admin",
            "admin123",
            "admin1234567",
            "administrator",
            "administrator1",
            "root",
            "iloveyou",
            "iloveyou1234",
            "monkey",
            "monkey123456",
            "dragon",
            "dragon123456",
            "sunshine",
            "sunshine1234",
            "princess",
            "princess1234",
            "football",
            "football1234",
            "baseball",
            "baseball1234",
            "master",
            "master123456",
            "shadow",
            "shadow123456",
            "superman",
            "superman1234",
            "trustno1",
            "trustno12345",
            "changeme",
            "changeme1234",
            "secret",
            "secret123456",
            "skychat",
            "skychat123",
            "skychat12345",
            "1q2w3e4r",
            "1q2w3e4r5t6y",
            "1qaz2wsx",
            "1qaz2wsx3edc",
            "zaq12wsx",
            "zaq12wsxcde3",
            "asdfghjkl",
            "asdfghjkl123"
    );

    /**
     * Result of a policy check. {@code code} is null when the password is acceptable.
     */
    public record Result(boolean accepted, String code, String message) {
        /**
         * Named {@code ok} rather than {@code accepted} because a record already generates the
         * {@code accepted()} accessor.
         */
        public static Result ok() {
            return new Result(true, null, null);
        }

        public static Result rejected(String code, String message) {
            return new Result(false, code, message);
        }
    }

    public Result validate(String password) {
        if (password == null || password.length() < MIN_LENGTH) {
            return Result.rejected(TOO_SHORT, "密码至少需要 " + MIN_LENGTH + " 位");
        }
        if (DENY_LIST.contains(password.toLowerCase(Locale.ROOT))) {
            return Result.rejected(TOO_COMMON, "该密码过于常见，请更换更复杂的密码");
        }
        if (characterClasses(password) < MIN_CLASSES) {
            return Result.rejected(
                    TOO_WEAK,
                    "密码需要包含大写字母、小写字母、数字、符号中的至少 " + MIN_CLASSES + " 类"
            );
        }
        return Result.ok();
    }

    public boolean isAcceptable(String password) {
        return validate(password).accepted();
    }

    private int characterClasses(String password) {
        boolean lower = false;
        boolean upper = false;
        boolean digit = false;
        boolean symbol = false;
        for (int index = 0; index < password.length(); index++) {
            char value = password.charAt(index);
            if (Character.isLowerCase(value)) {
                lower = true;
            } else if (Character.isUpperCase(value)) {
                upper = true;
            } else if (Character.isDigit(value)) {
                digit = true;
            } else {
                symbol = true;
            }
        }
        int classes = 0;
        classes += lower ? 1 : 0;
        classes += upper ? 1 : 0;
        classes += digit ? 1 : 0;
        classes += symbol ? 1 : 0;
        return classes;
    }
}
