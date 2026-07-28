"""Library (user's shelf) schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.book import BookSummary

LibraryStatus = Literal["want_to_read", "currently_reading", "read", "abandoned"]


class LibraryAdd(BaseModel):
    book_id: uuid.UUID
    status: LibraryStatus = "want_to_read"
    rating: float | None = Field(None, ge=0, le=5)
    review_text: str | None = Field(None, max_length=5000)


class LibraryUpdate(BaseModel):
    status: LibraryStatus | None = None
    rating: float | None = Field(None, ge=0, le=5)
    review_text: str | None = Field(None, max_length=5000)


class LibraryItem(BaseModel):
    book: BookSummary
    status: LibraryStatus | None = None
    rating: float | None = None
    review_text: str | None = None
    progress_percentage: float | None = None
    started_reading_at: datetime | None = None
    finished_reading_at: datetime | None = None
    last_interaction_at: datetime | None = None
    added_at: datetime
