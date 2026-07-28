"""Authentication endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from jose import JWTError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, rate_limit
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AuthenticationError, ConflictError
from app.core.logging_config import get_logger
from app.core.redis_client import cache_set
from app.core.security import (
    REFRESH_TOKEN,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User
from app.schemas.auth import (
    PreferenceQuiz,
    RefreshRequest,
    TokenPair,
    UserLogin,
    UserOut,
    UserRegister,
)
from app.schemas.common import Message
from app.services.ai.preference_learner import UserPreferenceLearner

logger = get_logger(__name__)
router = APIRouter()


def _token_pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    dependencies=[Depends(rate_limit(10, 3600, scope="register"))],
)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == payload.username.lower(),
                func.lower(User.email) == payload.email.lower(),
            )
        )
    )
    if existing is not None:
        # Deliberately vague about *which* field collided — enumerating
        # registered emails is a free gift to credential-stuffers.
        raise ConflictError(
            "An account with that username or email already exists.",
            code="account_exists",
        )

    user = User(
        username=payload.username.lower(),
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        reading_level=payload.reading_level,
        preferences={},
        last_active=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("Registered user %s", user.id)
    return _token_pair(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for a token pair",
    dependencies=[Depends(rate_limit(20, 900, scope="login"))],
)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    identifier = payload.identifier.strip().lower()
    user = await db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == identifier,
                func.lower(User.email) == identifier,
            )
        )
    )

    # Verify against a dummy hash when the user is missing so the response
    # time doesn't reveal whether the account exists.
    stored_hash = user.password_hash if user else "$2b$12$" + "x" * 53
    if not verify_password(payload.password, stored_hash) or user is None:
        raise AuthenticationError("Incorrect credentials.", code="invalid_credentials")

    user.last_active = datetime.now(UTC)
    await db.commit()
    return _token_pair(user)


@router.post("/refresh", response_model=TokenPair, summary="Rotate an expiring token")
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        claims = decode_token(payload.refresh_token, expected_type=REFRESH_TOKEN)
    except JWTError as exc:
        raise AuthenticationError(
            "Refresh token is invalid or expired.", code="invalid_refresh_token"
        ) from exc

    user = await db.get(User, claims["sub"])
    if user is None:
        raise AuthenticationError("User no longer exists.", code="user_not_found")
    return _token_pair(user)


@router.post("/logout", response_model=Message, summary="Revoke the current token")
async def logout(request: Request, user: User = Depends(get_current_user)):
    """Add the presented token's jti to the revocation list.

    JWTs are stateless, so 'logout' can only mean a denylist. The entry is kept
    for exactly the token's remaining lifetime; after that it expires anyway.
    If Redis is down the denylist silently no-ops and the token stays valid
    until expiry — the tradeoff we accept for a cache-optional design.
    """
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    if token:
        try:
            claims = decode_token(token)
            if jti := claims.get("jti"):
                await cache_set(
                    f"revoked:{jti}",
                    True,
                    ttl=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                )
        except JWTError:
            pass
    return Message(message="Signed out.")


@router.get("/me", response_model=UserOut, summary="Current user profile")
async def me(user: User = Depends(get_current_user)):
    return user


@router.post(
    "/preferences",
    response_model=UserOut,
    summary="Save preference-quiz answers",
)
async def save_preferences(
    payload: PreferenceQuiz,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store the cold-start quiz and immediately seed a preference vector.

    Building the embedding here rather than waiting for the 6-hourly batch is
    what lets a brand-new user get real recommendations on their first visit.
    """
    answers = payload.model_dump(exclude_none=True)
    user.preferences = {**(user.preferences or {}), **answers}
    if payload.reading_level:
        user.reading_level = payload.reading_level
    await db.commit()
    await db.refresh(user)

    learner = UserPreferenceLearner(db)
    try:
        await learner.update_user_preferences(user.id)
    except Exception as exc:
        # The quiz is saved either way; the vector will be built by the batch
        # job on the next run.
        logger.warning("Could not seed preference vector for %s: %s", user.id, exc)

    return user
