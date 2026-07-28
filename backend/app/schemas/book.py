"""Book catalogue schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class BookSummary(ORMModel):
    """Compact form used in lists and search results."""

    id: uuid.UUID
    title: str
    author: str
    cover_url: str | None = None
    genres: list[str] | None = None
    average_rating: float | None = None
    publication_year: int | None = None
    page_count: int | None = None


class BookOut(BookSummary):
    description: str | None = None
    isbn: str | None = None
    moods: dict[str, Any] | None = None
    reading_level: str | None = None
    rating_count: int = 0
    source_ids: dict[str, Any] | None = None
    created_at: datetime | None = None


class BookDetail(BookOut):
    """Book detail plus the requesting user's own state for it."""

    user_status: str | None = None
    user_rating: float | None = None
    user_progress_percentage: float | None = None
    has_playlist: bool = False
    similar_books: list[BookSummary] = Field(default_factory=list)


class BookSearchParams(BaseModel):
    q: str | None = Field(None, description="Free-text query over title/author")
    genre: str | None = None
    author: str | None = None
    min_rating: float | None = Field(None, ge=0, le=5)
    max_pages: int | None = Field(None, ge=1)
    reading_level: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    semantic: bool = Field(
        False,
        description=(
            "Rank by embedding similarity instead of keyword match. Slower but "
            "finds thematically related books that share no keywords."
        ),
    )
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)
