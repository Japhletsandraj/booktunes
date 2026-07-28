"""Shared FastAPI dependencies: auth, DB session, rate limiting."""

import uuid
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import AuthenticationError, RateLimitError
from app.core.redis_client import cache_get, rate_limit_hit
from app.core.security import ACCESS_TOKEN, decode_token
from app.models import User

# auto_error=False so we can raise our own enveloped 401 instead of FastAPI's.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.", code="missing_token")

    token = credentials.credentials
    try:
        payload = decode_token(token, expected_type=ACCESS_TOKEN)
    except JWTError as exc:
        raise AuthenticationError(
            "Token is invalid or has expired.", code="invalid_token"
        ) from exc

    # Logout revokes by jti; a token issued before logout must stop working
    # even though it hasn't expired yet.
    if jti := payload.get("jti"):
        if await cache_get(f"revoked:{jti}"):
            raise AuthenticationError("This token has been revoked.", code="token_revoked")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token subject.", code="invalid_token") from exc

    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("User no longer exists.", code="user_not_found")

    # Cheap liveness signal used by the "active users" batch jobs. Throttled to
    # once every 5 minutes so a chatty client doesn't write on every request.
    now = datetime.now(UTC)
    last = user.last_active
    if last is None or (now - last.replace(tzinfo=last.tzinfo or UTC)).total_seconds() > 300:
        user.last_active = now
        await db.commit()

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """For endpoints that personalise when signed in but work when not."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except AuthenticationError:
        return None


def rate_limit(limit: int, window_seconds: int = 60, scope: str = "default"):
    """Per-IP rate limit for a route.

    Applied to the expensive or abusable routes (auth, playlist generation),
    not globally — a blanket limit would throttle normal reading-progress
    polling, which is by design frequent.
    """

    async def _dependency(request: Request) -> None:
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        allowed, count = await rate_limit_hit(
            f"{scope}:{client_ip}", limit, window_seconds
        )
        if not allowed:
            raise RateLimitError(
                f"Rate limit exceeded: {limit} requests per {window_seconds}s.",
                details={"limit": limit, "window_seconds": window_seconds, "count": count},
            )

    return _dependency
