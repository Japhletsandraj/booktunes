"""User profile endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis_client import cache_delete_pattern
from app.models import User
from app.schemas.auth import UserOut, UserUpdate
from app.schemas.common import Message
from app.services.ai.preference_learner import UserPreferenceLearner
from app.services.reading_service import ReadingService

router = APIRouter()


@router.get("/me", response_model=UserOut, summary="Your profile")
async def get_profile(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut, summary="Update your profile")
async def update_profile(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me/stats", summary="Your reading statistics")
async def my_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ReadingService(db).get_reading_stats(user.id)


@router.get("/me/taste", summary="What the recommender has learned about you")
async def my_taste_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Expose the learned profile so recommendations aren't a black box.

    The raw 384-dim vector is useless to a client, so this reports the derived
    signal counts and confidence instead.
    """
    learner = UserPreferenceLearner(db)
    confidence = await learner.get_confidence(user.id)
    has_vector = await learner.get_user_embedding(user.id) is not None

    return {
        "has_preference_vector": has_vector,
        "confidence": round(confidence, 3),
        "confidence_label": (
            "high" if confidence >= 0.7
            else "moderate" if confidence >= 0.35
            else "low — rate a few more books to sharpen recommendations"
        ),
        "quiz_answers": user.preferences or {},
        "reading_level": user.reading_level,
    }


@router.post("/me/refresh-preferences", response_model=Message, summary="Recompute your taste profile")
async def refresh_preferences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force an immediate rebuild instead of waiting for the 6-hourly job."""
    result = await UserPreferenceLearner(db).update_user_preferences(user.id)
    await cache_delete_pattern(f"recs:v1:{user.id}*")

    if not result.get("updated"):
        return Message(
            message=(
                "Not enough activity yet — add or rate a few books and try again."
            ),
            success=False,
        )
    return Message(message=f"Taste profile rebuilt from {result['signals']} signals.")


@router.delete("/me", response_model=Message, summary="Delete your account")
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete the account.

    Every dependent table cascades on user_id, so this removes interactions,
    progress, recommendations, saved playlists and the preference vector too.
    """
    user_id = user.id
    await db.delete(user)
    await db.commit()
    await cache_delete_pattern(f"*{user_id}*")
    return Message(message="Your account and all associated data have been deleted.")
