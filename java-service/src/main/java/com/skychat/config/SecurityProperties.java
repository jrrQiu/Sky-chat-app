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
        Audit audit
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
}
