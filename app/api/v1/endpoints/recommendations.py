"""Recommendation endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, rate_limit
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.core.logging_config import get_logger
from app.core.redis_client import cache_delete
from app.models import Book, RecommendationFeedback, User
from app.schemas.book import BookSummary
from app.schemas.common import Message
from app.schemas.recommendation import (
    FactorContribution,
    PersonalizedSummary,
    RecommendationBatch,
    RecommendationFeedbackIn,
    RecommendationOut,
)
from app.services.ai.recommendation_engine import (
    FACTOR_WEIGHTS,
    BookRecommendationEngine,
)

logger = get_logger(__name__)
router = APIRouter()


def _factors_to_schema(factors: dict) -> list[FactorContribution]:
    out = []
    for name, weight in FACTOR_WEIGHTS.items():
        score = float(factors.get(name, 50.0))
        out.append(
            FactorContribution(
                name=name,
                score=score,
                weight=weight,
                contribution=round(score * weight, 2),
                explanation="",
            )
        )
    out.sort(key=lambda f: f.contribution, reverse=True)
    return out


@router.get(
    "/personalized",
    response_model=RecommendationBatch,
    summary="Personalized recommendations",
)
async def personalized(
    limit: int = Query(20, ge=1, le=50),
    genre: str | None = Query(None, description="Restrict to a single genre"),
    refresh: bool = Query(False, description="Bypass the cache and recompute"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = BookRecommendationEngine(db)
    payload = await engine.get_personalized_recommendations(
        user.id, limit=limit, use_cache=not refresh,
        genres=[genre] if genre else None,
    )

    if not payload["items"]:
        return RecommendationBatch(
            items=[], generated_at=datetime.now(UTC),
            cached=False, strategy=payload.get("strategy", {}),
        )

    book_ids = [uuid.UUID(item["book_id"]) for item in payload["items"]]
    books = {
        b.id: b
        for b in (await db.scalars(select(Book).where(Book.id.in_(book_ids)))).all()
    }

    items = []
    for item in payload["items"]:
        book = books.get(uuid.UUID(item["book_id"]))
        if book is None:
            continue
        items.append(
            RecommendationOut(
                book=BookSummary.model_validate(book),
                match_score=item["match_score"],
                confidence_score=item["confidence_score"],
                factors=_factors_to_schema(item["factors"]),
                playlist_available=item["playlist_available"],
            )
        )

    generated = payload["generated_at"]
    return RecommendationBatch(
        items=items,
        generated_at=(
            datetime.fromisoformat(generated) if isinstance(generated, str) else generated
        ),
        cached=payload.get("cached", False),
        strategy=payload.get("strategy", {}),
    )


@router.get(
    "/book/{book_id}/summary",
    response_model=PersonalizedSummary,
    summary="Why this book fits you",
)
async def book_summary(
    book_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    engine = BookRecommendationEngine(db)
    payload = await engine.generate_personalized_summary(user.id, book_id)
    if payload is None:
        raise NotFoundError(f"No book with id {book_id}.", code="book_not_found")

    return PersonalizedSummary(
        book_id=uuid.UUID(payload["book_id"]),
        user_id=uuid.UUID(payload["user_id"]),
        summary=payload["summary"],
        match_score=payload["match_score"],
        confidence_score=payload["confidence_score"],
        factors=[FactorContribution(**f) for f in payload["factors"]],
        generated_at=datetime.fromisoformat(payload["generated_at"]),
    )


@router.get(
    "/mood/{mood}",
    response_model=list[RecommendationOut],
    summary="Mood-based recommendations",
)
async def by_mood(
    mood: str,
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Books expressing a mood, then re-scored against this user's profile.

    Two-stage rather than one: filtering on mood first guarantees the results
    genuinely match the mood asked for, and personal scoring only decides the
    order within that set.
    """
    engine = BookRecommendationEngine(db)
    strength = Book.moods[mood].astext.cast(Float)
    exclude = await engine._seen_book_ids(user.id)

    candidates = (
        await db.scalars(
            select(Book)
            .where(Book.moods.has_key(mood), strength >= 0.4)
            .order_by(desc(strength))
            .limit(limit * 3)
        )
    ).all()
    candidates = [b for b in candidates if str(b.id) not in exclude]
    if not candidates:
        return []

    context = await engine._user_context(user.id)
    user_embedding = await engine.preferences.get_user_embedding(user.id)
    confidence = await engine.preferences.get_confidence(user.id)
    with_playlists = await engine._books_with_playlists([b.id for b in candidates])

    from app.services.ai import embeddings as emb

    scored = []
    for book in candidates:
        similarity = (
            emb.cosine_similarity(user_embedding, list(book.embedding))
            if user_embedding and book.embedding is not None
            else 0.4
        )
        factors = engine._score_factors(
            book, similarity, context, has_playlist=book.id in with_playlists
        )
        scored.append((book, engine._weighted_score(factors), factors))

    scored.sort(key=lambda triple: triple[1], reverse=True)
    final = engine._apply_diversity(scored, limit)

    return [
        RecommendationOut(
            book=BookSummary.model_validate(book),
            match_score=score,
            confidence_score=round(confidence * 100, 2),
            factors=_factors_to_schema(factors),
            playlist_available=book.id in with_playlists,
        )
        for book, score, factors in final
    ]


@router.post(
    "/feedback",
    response_model=Message,
    summary="Rate a recommendation",
    dependencies=[Depends(rate_limit(120, 3600, scope="feedback"))],
)
async def submit_feedback(
    payload: RecommendationFeedbackIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record explicit feedback and invalidate the user's cached list.

    Not invalidating here is what makes a recommender feel broken: a user
    dismisses a book, reloads, and it's still sitting at the top.
    """
    if await db.get(Book, payload.book_id) is None:
        raise NotFoundError(f"No book with id {payload.book_id}.", code="book_not_found")

    db.add(
        RecommendationFeedback(
            user_id=user.id,
            book_id=payload.book_id,
            feedback_type=payload.feedback_type,
            reason=payload.reason,
        )
    )
    await db.commit()

    await cache_delete(f"recs:v1:{user.id}")
    from app.core.redis_client import cache_delete_pattern

    await cache_delete_pattern(f"recs:v1:{user.id}*")

    return Message(message="Thanks — future recommendations will reflect this.")
