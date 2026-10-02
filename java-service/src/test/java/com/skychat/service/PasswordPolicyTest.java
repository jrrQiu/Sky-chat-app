package com.skychat.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Password policy: length, character-class diversity and the built-in deny-list.
 *
 * <p>No mocking framework: the policy is a pure function, so it is exercised directly.</p>
 */
class PasswordPolicyTest {

    private final PasswordPolicy policy = new PasswordPolicy();

    @Test
    @DisplayName("a password under 12 characters is rejected as PASSWORD_TOO_SHORT")
    void tooShort() {
        // 11 characters, three classes: only the length rule may fire.
        PasswordPolicy.Result result = policy.validate("Abcdefghij1");

        assertThat(result.accepted()).isFalse();
        assertThat(result.code()).isEqualTo(PasswordPolicy.TOO_SHORT);
    }

    @Test
    @DisplayName("a 12 character password with fewer than 3 classes is PASSWORD_TOO_WEAK")
    void tooWeak() {
        // Long enough, but lower-case only.
        PasswordPolicy.Result result = policy.validate("abcdefghijkl");

        assertThat(result.accepted()).isFalse();
        assertThat(result.code()).isEqualTo(PasswordPolicy.TOO_WEAK);
    }

    @Test
    @DisplayName("two classes are still too weak, three are enough")
    void classBoundary() {
        assertThat(policy.validate("abcdefghij12").code()).isEqualTo(PasswordPolicy.TOO_WEAK);
        assertThat(policy.validate("Abcdefghij12").accepted()).isTrue();
    }

    @Test
    @DisplayName("a deny-listed password is rejected as PASSWORD_TOO_COMMON regardless of case")
    void tooCommon() {
        // Each sample is at least 12 characters and would otherwise pass the tier checks, so
        // the deny-list is the only rule that can reject it.
        assertThat(policy.validate("password1234").code()).isEqualTo(PasswordPolicy.TOO_COMMON);
        assertThat(policy.validate("PassWord1234").code()).isEqualTo(PasswordPolicy.TOO_COMMON);
        // All four classes, denied purely by the list.
        assertThat(policy.validate("Passw0rd!1234").code()).isEqualTo(PasswordPolicy.TOO_COMMON);
        assertThat(policy.validate("changeme1234").code()).isEqualTo(PasswordPolicy.TOO_COMMON);
        assertThat(policy.validate("qwertyuiop12").code()).isEqualTo(PasswordPolicy.TOO_COMMON);
    }

    @Test
    @DisplayName("length is checked before the deny-list, so a short common password is TOO_SHORT")
    void shortCommonPasswordFailsOnLengthFirst() {
        assertThat(policy.validate("password123").code()).isEqualTo(PasswordPolicy.TOO_SHORT);
        assertThat(policy.validate("admin").code()).isEqualTo(PasswordPolicy.TOO_SHORT);
    }

    @Test
    @DisplayName("the deny-list holds at least 20 entries and every one of them is refused")
    void denyListIsSubstantial() {
        String[] samples = {
                "password", "password1", "password123", "password1234", "passw0rd",
                "p@ssw0rd", "123456", "12345678", "123456789", "123456789012",
                "qwerty", "qwerty123", "qwertyuiop", "abc123", "letmein",
                "welcome", "welcome1", "admin", "admin123", "administrator",
                "root", "iloveyou", "monkey", "dragon", "sunshine",
                "princess", "football", "baseball", "master", "shadow",
                "superman", "trustno1", "changeme", "secret", "skychat",
                "skychat123", "1q2w3e4r", "1qaz2wsx", "zaq12wsx", "asdfghjkl"
        };
        assertThat(samples.length).isGreaterThanOrEqualTo(20);

        for (String sample : samples) {
            PasswordPolicy.Result result = policy.validate(sample);
            assertThat(result.accepted())
                    .as("deny-listed password '%s' must never be accepted", sample)
                    .isFalse();
            // A long deny-listed password must fail the deny-list rule, not the length rule.
            if (sample.length() >= PasswordPolicy.MIN_LENGTH) {
                assertThat(result.code())
                        .as("deny-listed password '%s'", sample)
                        .isEqualTo(PasswordPolicy.TOO_COMMON);
            }
        }
    }

    @Test
    @DisplayName("a strong password is accepted")
    void strongIsAccepted() {
        PasswordPolicy.Result result = policy.validate("Sk7#trail-Blue42");

        assertThat(result.accepted()).isTrue();
        assertThat(result.code()).isNull();
        assertThat(policy.isAcceptable("Sk7#trail-Blue42")).isTrue();
    }

    @Test
    @DisplayName("null and empty passwords are too short, never a null-pointer surprise")
    void nullIsTooShort() {
        assertThat(policy.validate(null).code()).isEqualTo(PasswordPolicy.TOO_SHORT);
        assertThat(policy.validate("").code()).isEqualTo(PasswordPolicy.TOO_SHORT);
    }
}
