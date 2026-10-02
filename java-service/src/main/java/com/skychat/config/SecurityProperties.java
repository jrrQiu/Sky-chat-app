package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Security and hardening knobs under the {@code skychat.*} namespace.
 *
 * <p>Every default here is the safe one: self-registration is off, forwarded headers are
 * only trusted when an operator explicitly enables them, and the audit trail is kept for a
 * year.</p>
 */
@ConfigurationProperties(prefix = "skychat")
public record SecurityProperties(
        Auth auth,
        Http http,
        Audit audit,
        Invite invite,
        Bootstrap bootstrap
) {
    public record Auth(boolean selfRegistrationEnabled) {
    }

    public record Http(boolean trustForwardedFor) {
    }

    /**
     * {@code retentionMonths} is only a documented contract for the (future) purge job; the
     * service itself never deletes audit rows.
     */
    public record Audit(int retentionMonths) {
    }

    /**
     * Invitations replace open registration.
     *
     * @param baseUrl where the invite link should point, i.e. the front-end route that
     *                renders the set-password page. The raw token is appended as
     *                {@code ?token=}; it is never logged by the service.
     * @param ttlHours default validity, clamped to {@code [1, 720]}; a caller may override
     *                it per invitation. Re-issuing for the same address revokes any earlier
     *                pending link, so only one link is ever live.
     */
    public record Invite(String baseUrl, int ttlHours) {
    }

    /**
     * First-administrator bootstrap. When {@code adminEmail} is set and no active
     * administrator exists, startup issues an ordinary invitation for that address with
     * the {@code admin} role and logs the link.
     *
     * <p>Deliberately no password field: a password in an environment variable would have
     * to be transmitted, stored and rotated, whereas the invite flow already gives the
     * operator a one-time link and keeps the credential out of configuration entirely.</p>
     */
    public record Bootstrap(String adminEmail) {
    }
}
