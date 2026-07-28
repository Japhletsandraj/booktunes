"""Book catalogue endpoints."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set
from app.models import Book, BookPlaylist, ReadingProgress, User, UserBookInteraction
from app.schemas.book import BookDetail, BookSummary
from app.schemas.common import Page
from app.services.ai import embeddings
from app.utils.taxonomy import CANONICAL_GENRES, MOOD_VOCABULARY

logger = get_logger(__name__)
router = APIRouter()


@router.get("", response_model=Page[BookSummary], summary="Search the catalogue")
async def search_books(
    q: str | None = Query(None, description="Free-text query"),
    genre: str | None = None,
    author: str | None = None,
    min_rating: float | None = Query(None, ge=0, le=5),
    max_pages: int | None = Query(None, ge=1),
    reading_level: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    semantic: bool = Query(
        False,
        description=(
            "Rank by meaning rather than keywords — finds thematically similar "
            "books that share no words with the query."
        ),
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if genre:
        filters.append(Book.genres.any(genre))
    if author:
        filters.append(Book.author.ilike(f"%{author}%"))
    if min_rating is not None:
        filters.append(Book.average_rating >= min_rating)
    if max_pages is not None:
        filters.append(Book.page_count <= max_pages)
    if reading_level:
        filters.append(Book.reading_level == reading_level)
    if year_from is not None:
        filters.append(Book.publication_year >= year_from)
    if year_to is not None:
        filters.append(Book.publication_year <= year_to)

    if q and semantic:
        vector = await embeddings.embed_text(q)
        if vector is not None:
            distance = Book.embedding.cosine_distance(vector)
            query = (
                select(Book)
                .where(Book.embedding.is_not(None), *filters)
                .order_by(distance)
                .limit(limit)
                .offset(offset)
            )
            rows = (await db.scalars(query)).all()
            total = await db.scalar(
                select(func.count()).select_from(Book).where(
                    Book.embedding.is_not(None), *filters
                )
            )
            return Page(items=rows, total=int(total or 0), limit=limit, offset=offset)
        logger.warning("Semantic search unavailable; falling back to keyword search")

    if q:
        # websearch_to_tsquery handles quoted phrases and OR the way users
        # expect, and never raises on malformed input the way to_tsquery does.
        ts_query = func.websearch_to_tsquery("english", q)
        ts_vector = func.to_tsvector(
            "english", func.concat(Book.title, " ", Book.author)
        )
        filters.append(
            or_(ts_vector.op("@@")(ts_query), Book.title.ilike(f"%{q}%"))
        )

    base = select(Book).where(*filters) if filters else select(Book)
    total = await db.scalar(
        select(func.count()).select_from(Book).where(*filters)
        if filters else select(func.count()).select_from(Book)
    )
    rows = (
        await db.scalars(
            base.order_by(desc(Book.rating_count), desc(Book.average_rating))
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return Page(items=rows, total=int(total or 0), limit=limit, offset=offset)


@router.get("/trending", response_model=list[BookSummary], summary="Trending books")
async def trending_books(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Books with the most recent interaction activity.

    'Trending' here means recent engagement inside Booktunes, not global sales.
    Before the platform has traffic this falls back to a damped rating ranking
    so the endpoint is never empty.
    """
    cache_key = f"books:trending:{limit}"
    if cached := await cache_get(cache_key):
        return cached

    window_start = datetime.now(UTC) - timedelta(days=7)
    rows = (
        await db.execute(
            select(Book, func.count(UserBookInteraction.id).label("activity"))
            .join(UserBookInteraction, UserBookInteraction.book_id == Book.id)
            .where(UserBookInteraction.last_interaction_at >= window_start)
            .group_by(Book.id)
            .order_by(desc("activity"))
            .limit(limit)
        )
    ).all()

    books = [row[0] for row in rows]

    if len(books) < limit:
        prior_votes, prior_mean = 50, 3.5
        damped = (
            func.coalesce(Book.average_rating, prior_mean)
            * func.coalesce(Book.rating_count, 0)
            + prior_mean * prior_votes
        ) / (func.coalesce(Book.rating_count, 0) + prior_votes)

        existing_ids = {b.id for b in books}
        extra = (
            await db.scalars(
                select(Book)
                .where(Book.id.notin_(existing_ids) if existing_ids else True)
                .order_by(desc(damped))
                .limit(limit - len(books))
            )
        ).all()
        books.extend(extra)

    payload = [BookSummary.model_validate(b).model_dump(mode="json") for b in books]
    await cache_set(cache_key, payload, ttl=1800)
    return payload


@router.get("/genres", response_model=list[str], summary="Available genres")
async def list_genres():
    return CANONICAL_GENRES


@router.get("/moods", response_model=list[str], summary="Available moods")
async def list_moods():
    return MOOD_VOCABULARY


@router.get(
    "/genre/{genre}", response_model=Page[BookSummary], summary="Books in a genre"
)
async def books_by_genre(
    genre: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    condition = Book.genres.any(genre)
    total = await db.scalar(select(func.count()).select_from(Book).where(condition))
    rows = (
        await db.scalars(
            select(Book)
            .where(condition)
            .order_by(desc(Book.average_rating), desc(Book.rating_count))
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(items=rows, total=int(total or 0), limit=limit, offset=offset)


@router.get(
    "/mood/{mood}", response_model=list[BookSummary], summary="Books matching a mood"
)
async def books_by_mood(
    mood: str,
    limit: int = Query(20, ge=1, le=50),
    min_strength: float = Query(0.4, ge=0, le=1),
    db: AsyncSession = Depends(get_db),
):
    """Rank by how strongly a book expresses the requested mood.

    `moods` is JSONB scored 0-1 per mood, so we cast the value out and sort on
    it rather than treating mood as a boolean tag.
    """
    strength = Book.moods[mood].astext.cast(Float)
    rows = (
        await db.scalars(
            select(Book)
            .where(Book.moods.has_key(mood), strength >= min_strength)
            .order_by(desc(strength), desc(Book.average_rating))
            .limit(limit)
        )
    ).all()
    return rows


@router.get("/{book_id}", response_model=BookDetail, summary="Book detail")
async def get_book(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    book = await db.get(Book, book_id)
    if book is None:
        raise NotFoundError(f"No book with id {book_id}.", code="book_not_found")

    detail = BookDetail.model_validate(book)

    detail.has_playlist = bool(
        await db.scalar(
            select(BookPlaylist.id).where(BookPlaylist.book_id == book_id).limit(1)
        )
    )

    if book.embedding is not None:
        distance = Book.embedding.cosine_distance(list(book.embedding))
        similar = (
            await db.scalars(
                select(Book)
                .where(Book.id != book_id, Book.embedding.is_not(None))
                .order_by(distance)
                .limit(6)
            )
        ).all()
        detail.similar_books = [BookSummary.model_validate(b) for b in similar]

    if user is not None:
        interaction = await db.scalar(
            select(UserBookInteraction).where(
                UserBookInteraction.user_id == user.id,
                UserBookInteraction.book_id == book_id,
            )
        )
        if interaction:
            detail.user_status = interaction.status
            detail.user_rating = (
                float(interaction.rating) if interaction.rating is not None else None
            )
        progress = await db.scalar(
            select(ReadingProgress.percentage).where(
                ReadingProgress.user_id == user.id,
                ReadingProgress.book_id == book_id,
            )
        )
        if progress is not None:
            detail.user_progress_percentage = float(progress)

    return detail
