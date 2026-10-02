package com.skychat.support;

import com.skychat.config.AuthProperties;
import com.skychat.config.SecurityProperties;
import com.skychat.mapper.UserMapper;
import com.skychat.service.AuthService;
import com.skychat.service.JwtService;
import com.skychat.service.JwtSigner;
import com.skychat.service.PasswordPolicy;
import com.skychat.service.ratelimit.InMemoryRateLimitStore;
import com.skychat.service.ratelimit.RateLimiter;
import com.skychat.service.ratelimit.RedisRateLimitStore;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Shared wiring for the service-level tests so each test file states only what it varies.
 *
 * <p>The rate limiter is built without Redis on purpose: that exercises the documented
 * per-replica fallback and keeps these tests free of a network dependency.</p>
 */
public final class TestFixture {
    public static final String INVITE_BASE_URL = "http://localhost:5173/invite";
    public static final int INVITE_TTL_HOURS = 72;

    private TestFixture() {
    }

    public static SecurityProperties securityProperties(boolean selfRegistrationEnabled) {
        return securityProperties(selfRegistrationEnabled, INVITE_BASE_URL, INVITE_TTL_HOURS);
    }

    public static SecurityProperties securityProperties(
            boolean selfRegistrationEnabled,
            String inviteBaseUrl,
            int inviteTtlHours
    ) {
        return new SecurityProperties(
                new SecurityProperties.Auth(selfRegistrationEnabled),
                new SecurityProperties.Http(false),
                new SecurityProperties.Audit(12),
                new SecurityProperties.Invite(inviteBaseUrl, inviteTtlHours),
                new SecurityProperties.Bootstrap("")
        );
    }

    public static JwtService jwtService() {
        return new JwtService(
                new AuthProperties("unit-test-user-secret", 3600),
                new JwtSigner(new ObjectMapper())
        );
    }

    public static RateLimiter rateLimiter() {
        return new RateLimiter(new RedisRateLimitStore(null), new InMemoryRateLimitStore());
    }

    public static AuthService authService(UserMapper userMapper) {
        return authService(userMapper, securityProperties(false));
    }

    public static AuthService authService(UserMapper userMapper, SecurityProperties properties) {
        return new AuthService(
                userMapper,
                jwtService(),
                new PasswordPolicy(),
                properties,
                rateLimiter()
        );
    }
}
