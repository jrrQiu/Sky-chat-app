package com.skychat.service;

import com.skychat.config.AuthProperties;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

/**
 * Issues and verifies the end-user bearer tokens.
 *
 * <p>The HS256 mechanics live in {@link JwtSigner}; this service only maps them onto the user
 * token shape ({@code sub}/{@code email}/{@code roles}) the front end relies on.</p>
 */
@Service
public class JwtService {
    public record Claims(String userId, String email, List<String> roles, long expiresAt) {
    }

    private final AuthProperties properties;
    private final JwtSigner signer;

    public JwtService(AuthProperties properties, JwtSigner signer) {
        this.properties = properties;
        this.signer = signer;
    }

    public String createToken(String userId, String email, List<String> roles) {
        long now = Instant.now().getEpochSecond();
        return signer.signUserToken(
                properties.jwtSecret(),
                userId,
                email,
                roles,
                now + properties.jwtTtlSeconds()
        );
    }

    public Claims verify(String token) {
        JwtSigner.UserToken decoded = signer.verifyUserToken(properties.jwtSecret(), token);
        return new Claims(
                decoded.userId(),
                decoded.email(),
                decoded.roles(),
                decoded.expiresAt()
        );
    }
}
