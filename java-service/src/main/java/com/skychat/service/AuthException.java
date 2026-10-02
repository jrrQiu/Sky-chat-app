package com.skychat.service;

/**
 * A request rejected by a security rule, carrying a stable machine-readable {@code code}.
 *
 * <p>The HTTP layer maps {@code code} onto a status code; the message stays human readable
 * (and is in Chinese where the rest of the user-facing text is).</p>
 */
public class AuthException extends RuntimeException {
    private final String code;

    public AuthException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
