"""Helpers for building a signed internal caller identity in tests."""

import datetime as dt

import jwt

from app.config import settings


def mint_token(
    user_id: str = "u1",
    roles: list[str] | None = None,
    *,
    ttl_seconds: int = 60,
    secret: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """Mint the same shape of token the Java service mints per call."""
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "iss": issuer or settings.agent_internal_jwt_issuer,
        "aud": audience or settings.agent_internal_jwt_audience,
        "sub": user_id,
        "roles": list(roles or []),
        "iat": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
        "jti": f"test-{user_id}",
    }
    return jwt.encode(
        payload,
        secret if secret is not None else settings.agent_internal_jwt_secret,
        algorithm="HS256",
    )


def auth_headers(
    user_id: str = "u1",
    roles: list[str] | None = None,
    *,
    accept: str = "application/json",
) -> dict[str, str]:
    return {
        "Accept": accept,
        "Authorization": f"Bearer {mint_token(user_id, roles)}",
        # Still sent as a non-authoritative hint; the signed subject wins.
        "X-User-ID": user_id,
    }
