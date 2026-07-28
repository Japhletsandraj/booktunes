"""Reading progress, sync and stats schemas."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.book import BookSummary
from app.schemas.common import ORMModel


class Bookmark(BaseModel):
    page: int = Field(..., ge=0)
    label: str | None = Field(None, max_length=200)
    created_at: datetime | None = None


class Note(BaseModel):
    page: int = Field(..., ge=0)
    text: str = Field(..., max_length=5000)
    created_at: datetime | None = None


class ProgressUpdate(BaseModel):
    book_id: uuid.UUID
    current_page: int | None = Field(None, ge=0)
    percentage: float | None = Field(None, ge=0, le=100)
    session_seconds: int = Field(
        0, ge=0, le=86_400, description="Time read since the last update"
    )
    device_type: str | None = Field(None, max_length=50)
    bookmarks: list[Bookmark] | None = None
    notes: list[Note] | None = None
    # Client's last known version. When supplied and stale, the server reports
    # a conflict instead of silently clobbering another device's write.
    base_version: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _need_a_position(self):
        if self.current_page is None and self.percentage is None:
            raise ValueError("Provide at least one of current_page or percentage.")
        return self


class ProgressOut(ORMModel):
    book_id: uuid.UUID
    current_page: int
    percentage: float
    reading_speed: float | None = None
    total_time_spent: int
    last_read_at: datetime
    device_type: str | None = None
    sync_version: int
    bookmarks: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[dict[str, Any]] = Field(default_factory=list)


class CurrentlyReadingItem(BaseModel):
    book: BookSummary
    progress: ProgressOut
    estimated_minutes_left: int | None = None


class SyncResult(BaseModel):
    book_id: uuid.UUID
    status: Literal["applied", "conflict", "unchanged", "error"]
    sync_version: int
    progress: ProgressOut | None = None
    message: str | None = None


class BatchSyncRequest(BaseModel):
    # Capped at 10 so one request can't monopolise a free-tier worker.
    updates: list[ProgressUpdate] = Field(..., min_length=1, max_length=10)


class BatchSyncResponse(BaseModel):
    results: list[SyncResult]
    applied: int
    failed: int
    server_time: datetime


class ReadingStats(BaseModel):
    total_books_read: int = 0
    total_books_in_progress: int = 0
    total_pages_read: int = 0
    total_reading_seconds: int = 0
    average_reading_speed: float | None = Field(
        None, description="Pages per minute across all sessions"
    )
    average_completion_rate: float = 0.0
    current_streak_days: int = 0
    longest_streak_days: int = 0
    favorite_genres: list[dict[str, Any]] = Field(default_factory=list)
    most_productive_hour: int | None = None
    period_days: int | None = None
