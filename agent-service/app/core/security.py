import datetime as dt
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


bearer_scheme = HTTPBearer(auto_error=False)


def create_internal_token(user_id: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": now + dt.timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.agent_service_jwt_secret, algorithm="HS256")


def verify_internal_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.agent_service_jwt_secret,
        algorithms=["HS256"],
    )


async def require_internal_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    configured_token = settings.agent_service_token
    presented_token = credentials.credentials if credentials else ""

    if configured_token and presented_token != configured_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )

    header_user_id = request.headers.get("X-User-ID")
    if configured_token and header_user_id:
        return header_user_id

    if not configured_token:
        # In local development without an explicit token, accept a short-lived JWT
        # only when it is explicitly supplied.
        if not presented_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing internal token",
            )
        try:
            payload = verify_internal_token(presented_token)
            return str(payload["sub"])
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal JWT",
            ) from exc

    return header_user_id or "internal-agent-service"
