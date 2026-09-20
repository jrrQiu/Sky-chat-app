package com.skychat.service;

import com.skychat.domain.UserAccount;
import com.skychat.mapper.UserMapper;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.UUID;

@Service
public class AuthService {
    public record AuthSession(String token, UserAccount user) {
    }

    private static final int PBKDF2_ITERATIONS = 120_000;
    private static final int PBKDF2_KEY_BITS = 256;
    private static final int SALT_BYTES = 16;
    private static final Base64.Encoder B64 = Base64.getEncoder();
    private static final Base64.Decoder B64_DECODER = Base64.getDecoder();
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private final UserMapper userMapper;
    private final JwtService jwtService;

    public AuthService(UserMapper userMapper, JwtService jwtService) {
        this.userMapper = userMapper;
        this.jwtService = jwtService;
    }

    public AuthSession register(String email, String password, String name) {
        String normalizedEmail = normalizeEmail(email);
        if (!isValidPassword(password)) {
            throw new IllegalArgumentException("密码至少需要 6 位");
        }
        if (userMapper.findByEmail(normalizedEmail) != null) {
            throw new IllegalArgumentException("该邮箱已经注册");
        }

        UserAccount user = new UserAccount();
        user.setId(UUID.randomUUID().toString());
        user.setEmail(normalizedEmail);
        user.setName(name == null || name.isBlank() ? normalizedEmail.split("@")[0] : name.trim());
        user.setPasswordHash(hashPassword(password));
        user.setCreatedAt(LocalDateTime.now());
        userMapper.insert(user);
        return new AuthSession(jwtService.createToken(user.getId(), user.getEmail()), user);
    }

    public AuthSession login(String email, String password) {
        String normalizedEmail = normalizeEmail(email);
        UserAccount user = userMapper.findByEmail(normalizedEmail);
        if (user == null || !verifyPassword(password, user.getPasswordHash())) {
            throw new IllegalArgumentException("邮箱或密码错误");
        }
        return new AuthSession(jwtService.createToken(user.getId(), user.getEmail()), user);
    }

    public UserAccount findById(String userId) {
        return userMapper.findById(userId);
    }

    private String normalizeEmail(String email) {
        if (email == null || email.isBlank()) {
            throw new IllegalArgumentException("邮箱不能为空");
        }
        return email.trim().toLowerCase();
    }

    private boolean isValidPassword(String password) {
        return password != null && password.length() >= 6;
    }

    private String hashPassword(String password) {
        byte[] salt = new byte[SALT_BYTES];
        SECURE_RANDOM.nextBytes(salt);
        byte[] hash = pbkdf2(password.toCharArray(), salt, PBKDF2_ITERATIONS);
        return "pbkdf2_sha256$"
                + PBKDF2_ITERATIONS + "$"
                + B64.encodeToString(salt) + "$"
                + B64.encodeToString(hash);
    }

    private boolean verifyPassword(String password, String encodedHash) {
        if (encodedHash == null) {
            return false;
        }

        String[] parts = encodedHash.split("\\$");
        if (parts.length != 4 || !"pbkdf2_sha256".equals(parts[0])) {
            return false;
        }

        try {
            int iterations = Integer.parseInt(parts[1]);
            byte[] salt = B64_DECODER.decode(parts[2]);
            byte[] expected = B64_DECODER.decode(parts[3]);
            byte[] actual = pbkdf2(password.toCharArray(), salt, iterations);
            return MessageDigest.isEqual(expected, actual);
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    private byte[] pbkdf2(char[] password, byte[] salt, int iterations) {
        try {
            PBEKeySpec spec = new PBEKeySpec(password, salt, iterations, PBKDF2_KEY_BITS);
            SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
            return factory.generateSecret(spec).getEncoded();
        } catch (Exception error) {
            throw new IllegalStateException("Unable to hash password", error);
        }
    }
}
