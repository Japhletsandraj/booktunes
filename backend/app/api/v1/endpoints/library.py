"""User library (shelf) endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.core.redis_client import cache_delete, cache_delete_pattern
from app.models import Book, ReadingProgress, User, UserBookInteraction
from app.schemas.book import BookSummary
from app.schemas.common import Message, Page
from app.schemas.library import LibraryAdd, LibraryItem, LibraryUpdate

router = APIRouter()


async def _invalidate(user_id: uuid.UUID) -> None:
    """A library change invalidates recommendations and stats."""
    await cache_delete_pattern(f"recs:v1:{user_id}*")
    await cache_delete_pattern(f"stats:v1:{user_id}*")
    await cache_delete(f"userpref:v1:{user_id}")


@router.get("", response_model=Page[LibraryItem], summary="Your library")
async def get_library(
    status_filter: str | None = Query(
        None, alias="status",
        description="want_to_read | currently_reading | read | abandoned",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = [UserBookInteraction.user_id == user.id]
    if status_filter:
        conditions.append(UserBookInteraction.status == status_filter)

    total = await db.scalar(
        select(func.count()).select_from(UserBookInteraction).where(*conditions)
    )
    rows = (
        await db.execute(
            select(UserBookInteraction, Book, ReadingProgress.percentage)
            .join(Book, Book.id == UserBookInteraction.book_id)
            .outerjoin(
                ReadingProgress,
                (ReadingProgress.book_id == UserBookInteraction.book_id)
                & (ReadingProgress.user_id == user.id),
            )
            .where(*conditions)
            .order_by(desc(UserBookInteraction.updated_at))
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items = [
        LibraryItem(
            book=BookSummary.model_validate(book),
            status=interaction.status,
            rating=float(interaction.rating) if interaction.rating is not None else None,
            review_text=interaction.review_text,
            progress_percentage=float(pct) if pct is not None else None,
            started_reading_at=interaction.started_reading_at,
            finished_reading_at=interaction.finished_reading_at,
            last_interaction_at=interaction.last_interaction_at,
            added_at=interaction.created_at,
        )
        for interaction, book, pct in rows
    ]
    return Page(items=items, total=int(total or 0), limit=limit, offset=offset)


@router.post(
    "",
    response_model=LibraryItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add a book to your library",
)
async def add_to_library(
    payload: LibraryAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, payload.book_id)
    if book is None:
        raise NotFoundError(f"No book with id {payload.book_id}.", code="book_not_found")

    now = datetime.now(UTC)
    interaction = await db.scalar(
        select(UserBookInteraction).where(
            UserBookInteraction.user_id == user.id,
            UserBookInteraction.book_id == payload.book_id,
        )
    )

    if interaction is None:
        interaction = UserBookInteraction(
            user_id=user.id, book_id=payload.book_id, last_interaction_at=now,
        )
        db.add(interaction)
    # Already present: treat as an update so re-adding is idempotent rather
    # than a 409 the client has to special-case.

    interaction.status = payload.status
    if payload.rating is not None:
        interaction.rating = payload.rating
    if payload.review_text is not None:
        interaction.review_text = payload.review_text
    interaction.last_interaction_at = now
    interaction.interaction_count = (interaction.interaction_count or 0) + 1

    if payload.status == "currently_reading" and not interaction.started_reading_at:
        interaction.started_reading_at = now
    if payload.status == "read" and not interaction.finished_reading_at:
        interaction.finished_reading_at = now

    await db.commit()
    await db.refresh(interaction)
    await _invalidate(user.id)

    return LibraryItem(
        book=BookSummary.model_validate(book),
        status=interaction.status,
        rating=float(interaction.rating) if interaction.rating is not None else None,
        review_text=interaction.review_text,
        progress_percentage=None,
        started_reading_at=interaction.started_reading_at,
        finished_reading_at=interaction.finished_reading_at,
        last_interaction_at=interaction.last_interaction_at,
        added_at=interaction.created_at,
    )


@router.put(
    "/{book_id}", response_model=LibraryItem, summary="Update a library entry"
)
async def update_library_entry(
    book_id: uuid.UUID,
    payload: LibraryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    interaction = await db.scalar(
        select(UserBookInteraction).where(
            UserBookInteraction.user_id == user.id,
            UserBookInteraction.book_id == book_id,
        )
    )
    if interaction is None:
        raise NotFoundError("That book isn't in your library.", code="not_in_library")

    now = datetime.now(UTC)
    if payload.status is not None:
        interaction.status = payload.status
        if payload.status == "currently_reading" and not interaction.started_reading_at:
            interaction.started_reading_at = now
        if payload.status == "read" and not interaction.finished_reading_at:
            interaction.finished_reading_at = now
    if payload.rating is not None:
        interaction.rating = payload.rating
    if payload.review_text is not None:
        interaction.review_text = payload.review_text
    interaction.last_interaction_at = now

    await db.commit()
    await db.refresh(interaction)
    await _invalidate(user.id)

    book = await db.get(Book, book_id)
    pct = await db.scalar(
        select(ReadingProgress.percentage).where(
            ReadingProgress.user_id == user.id, ReadingProgress.book_id == book_id
        )
    )
    return LibraryItem(
        book=BookSummary.model_validate(book),
        status=interaction.status,
        rating=float(interaction.rating) if interaction.rating is not None else None,
        review_text=interaction.review_text,
        progress_percentage=float(pct) if pct is not None else None,
        started_reading_at=interaction.started_reading_at,
        finished_reading_at=interaction.finished_reading_at,
        last_interaction_at=interaction.last_interaction_at,
        added_at=interaction.created_at,
    )


@router.delete(
    "/{book_id}", response_model=Message, summary="Remove a book from your library"
)
async def remove_from_library(
    book_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    interaction = await db.scalar(
        select(UserBookInteraction).where(
            UserBookInteraction.user_id == user.id,
            UserBookInteraction.book_id == book_id,
        )
    )
    if interaction is None:
        raise NotFoundError("That book isn't in your library.", code="not_in_library")

    await db.delete(interaction)

    # Reading progress is kept deliberately: re-adding a book should restore
    # where you were, not reset you to page one.
    await db.commit()
    await _invalidate(user.id)
    return Message(message="Removed from your library. Your reading progress was kept.")
