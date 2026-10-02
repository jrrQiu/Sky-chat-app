package com.skychat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Rate-limit defaults for the guarded endpoints, all overridable under
 * {@code skychat.rate-limit.*}.
 *
 * <p>Every window is fixed: the counter starts on the first hit and the limit resets when the
 * window expires, so {@code retryAfterSeconds} is the remaining window.</p>
 */
@ConfigurationProperties(prefix = "skychat.rate-limit")
public record RateLimitProperties(
        @DefaultValue Login login,
        @DefaultValue Register register,
        @DefaultValue("60") Chat chat,
        @DefaultValue("30") ApprovalDecision approvalDecision,
        @DefaultValue InvitationAccept invitationAccept,
        @DefaultValue Admin admin
) {
    /**
     * {@code login}: 10 per 5 min per IP and 5 per 5 min per email.
     */
    public record Login(
            @DefaultValue("10") Limit perIp,
            @DefaultValue("5") Limit perEmail
    ) {
    }

    /**
     * {@code register}: 5 per hour per IP.
     */
    public record Register(@DefaultValue("5") Limit perIp) {
    }

    /**
     * {@code chat}: 60 per minute per user.
     */
    public record Chat(Limit perUser) {
    }

    /**
     * {@code approval-decision}: 30 per minute per user.
     */
    public record ApprovalDecision(Limit perUser) {
    }

    /**
     * {@code invitation-accept}: 10 per minute per IP. The endpoint is unauthenticated and
     * guesses at a token, so the window has to be keyed on the source address.
     */
    public record InvitationAccept(@DefaultValue("10") Limit perIp) {
    }

    /**
     * {@code admin}: 120 per minute per user, a coarse ceiling on the whole console. The
     * finer-grained protection is the role gate, not the counter.
     */
    public record Admin(@DefaultValue("120") Limit perUser) {
    }

    /**
     * A named window limit, e.g. {@code skychat.rate-limit.login.per-ip.limit=10}.
     */
    public record Limit(@DefaultValue("10") int limit) {
    }
}
