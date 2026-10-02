"""Caller authentication for the agent service.

The agent service sits behind the Java business service and must never take a
caller's identity from a request header. The Java service mints a short-lived
HS256 JWT per call (`iss`/`aud`/`sub`/`roles`/`exp`); that signature is the only
thing that establishes *who* is calling. `X-User-ID` is accepted as a hint for
logging but is never an authority, and the legacy static shared token is only
usable in an explicitly opted-in non-production configuration.
"""

import datetime as dt
import hmac
import logging
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Caller:
    """Authenticated caller identity used for authorisation and auditing."""

    user_id: str
    roles: list[str] = field(default_factory=list)
    actor_type: str = "user"
    source_ip: str = ""
    user_agent: str = ""

    def has_any_role(self, required: list[str] | tuple[str, ...]) -> bool:
        wanted = {role.strip() for role in required if role and role.strip()}
        if not wanted:
            return True
        if "admin" in self.roles:
            return True
        return bool(wanted.intersection(self.roles))


def create_internal_token(user_id: str) -> str:
    """Legacy helper kept for local tests and tooling."""
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, object] = {
        "sub": user_id,
        "iat": now,
        "exp": now + dt.timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.agent_service_jwt_secret, algorithm="HS256")


def verify_internal_token(token: str) -> dict[str, object]:
    return jwt.decode(
        token,
        settings.agent_service_jwt_secret,
        algorithms=["HS256"],
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
    )


def _client_ip(request: Request) -> str:
    # Only trust the forwarding header when explicitly configured; otherwise a
    # caller could choose its own audit identity.
    if getattr(settings, "trust_forwarded_for", False):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    client = request.client
    return (client.host if client else "")[:64]


def _verify_service_jwt(token: str) -> Caller:
    """Verify the Java-minted service JWT. Raises on any mismatch."""
    try:
        payload = jwt.decode(
            token,
            settings.agent_internal_jwt_secret,
            algorithms=["HS256"],
            audience=settings.agent_internal_jwt_audience,
            issuer=settings.agent_internal_jwt_issuer,
            leeway=settings.agent_internal_jwt_leeway_seconds,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Internal token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized(f"Invalid internal token: {exc}") from exc

    subject = str(payload.get("sub") or "")
    if not subject:
        raise _unauthorized("Internal token has no subject")

    raw_roles = payload.get("roles") or []
    if isinstance(raw_roles, str):
        roles = [role for role in raw_roles.split(",") if role]
    else:
        roles = [str(role) for role in raw_roles]

    return Caller(user_id=subject, roles=roles, actor_type="user")


def resolve_caller(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> Caller:
    presented = credentials.credentials if credentials else ""
    claimed_user = request.headers.get("X-User-ID") or ""
    source_ip = _client_ip(request)
    user_agent = (request.headers.get("User-Agent") or "")[:255]

    if settings.agent_internal_jwt_secret:
        if not presented:
            raise _unauthorized("Missing internal token")
        caller = _verify_service_jwt(presented)
        if claimed_user and claimed_user != caller.user_id:
            # Not fatal, but it means a caller is trying to act as someone else.
            logger.warning(
                "X-User-ID %r does not match the signed subject %r; using the token",
                claimed_user,
                caller.user_id,
            )
        return Caller(
            user_id=caller.user_id,
            roles=caller.roles,
            actor_type=caller.actor_type,
            source_ip=source_ip,
            user_agent=user_agent,
        )

    if settings.agent_service_token:
        if not settings.allow_static_internal_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="INTERNAL_AUTH_NOT_CONFIGURED",
            )
        if not hmac.compare_digest(presented, settings.agent_service_token):
            raise _unauthorized("Invalid internal token")
        logger.warning(
            "Authenticating with the legacy static token; set "
            "AGENT_INTERNAL_JWT_SECRET to enable signed caller identity"
        )
        return Caller(
            user_id=claimed_user or "internal-agent-service",
            roles=[],
            actor_type="service",
            source_ip=source_ip,
            user_agent=user_agent,
        )

    if presented:
        try:
            payload = verify_internal_token(presented)
        except Exception as exc:  # noqa: BLE001 - normalised to 401 below
            raise _unauthorized("Invalid internal JWT") from exc
        return Caller(
            user_id=str(payload["sub"]),
            roles=[str(role) for role in (payload.get("roles") or [])],
            actor_type="user",
            source_ip=source_ip,
            user_agent=user_agent,
        )

    raise _unauthorized("Missing internal token")


async def require_caller(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Caller:
    """Dependency returning the full authenticated caller (for audit logging)."""
    return resolve_caller(request, credentials)


async def require_internal_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Backwards-compatible dependency returning just the caller's user id."""
    return resolve_caller(request, credentials).user_id


def internal_auth_problem() -> str | None:
    """Describe an unusable production auth configuration, if any."""
    if not settings.is_production or settings.agent_internal_jwt_secret:
        return None
    if settings.agent_service_token and settings.allow_static_internal_token:
        return (
            "ENVIRONMENT=production must not authenticate callers with the static "
            "AGENT_SERVICE_TOKEN; configure AGENT_INTERNAL_JWT_SECRET so the caller "
            "identity is signed"
        )
    return (
        "ENVIRONMENT=production requires AGENT_INTERNAL_JWT_SECRET so the agent "
        "service can verify signed caller identity"
    )
