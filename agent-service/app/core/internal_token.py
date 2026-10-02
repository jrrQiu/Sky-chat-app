"""Mint the token the agent service presents when it calls back into Java.

The two directions use the same shared secret but **different issuer/audience
pairs**. Java mints ``iss=sky-chat-java-service`` / ``aud=sky-chat-agent-service``
and this module mints ``iss=sky-chat-agent-service`` / ``aud=sky-chat-java-service``.

That asymmetry is the whole point. With one shared pair, a token Java minted for
the agent service -- or anything that merely observed one -- could be presented
back to Java and accepted as authority to act as the user it named. Because each
side checks both claims, a token is only ever accepted by the service it was
addressed to, and the subject stays authoritative in both directions.

Minting is deliberately separate from :mod:`app.core.security`: verification is
about refusing things, and mixing the two makes it easy to accidentally trust a
locally minted token.
"""

from __future__ import annotations

import datetime as dt
import uuid

import jwt

from app.config import settings


class CallerTokenUnavailable(RuntimeError):
    """Raised when no shared secret is configured, i.e. Java cannot be called."""


def caller_token_available() -> bool:
    return bool(settings.agent_internal_jwt_secret)


def mint_java_caller_token(
    user_id: str,
    roles: list[str] | None = None,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """A short-lived token naming the user the agent is acting for.

    ``user_id`` becomes the signed subject, which is what Java uses as the acting
    identity. It is never read from a header on the Java side.
    """
    secret = settings.agent_internal_jwt_secret
    if not secret:
        raise CallerTokenUnavailable(
            "AGENT_INTERNAL_JWT_SECRET is not configured; the agent service cannot "
            "authenticate its tool calls to the Java service"
        )
    if not user_id or not user_id.strip():
        # An anonymous tool call would either be rejected downstream or, worse,
        # executed against an empty identity.
        raise ValueError("a caller token needs a subject")

    now = dt.datetime.now(dt.timezone.utc)
    ttl = settings.agent_caller_ttl_seconds if ttl_seconds is None else ttl_seconds
    payload = {
        "iss": settings.agent_caller_issuer,
        "aud": settings.java_service_audience,
        "sub": user_id.strip(),
        "roles": list(roles or []),
        "iat": now,
        "exp": now + dt.timedelta(seconds=max(1, ttl)),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm="HS256")
