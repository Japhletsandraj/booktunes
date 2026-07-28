"""Reading progress and sync endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.book import BookSummary
from app.schemas.reading import (
    BatchSyncRequest,
    BatchSyncResponse,
    CurrentlyReadingItem,
    ProgressOut,
    ProgressUpdate,
    ReadingStats,
    SyncResult,
)
from app.services.reading_service import SYNC_INTERVAL_SECONDS, ReadingService

router = APIRouter()


@router.get(
    "/currently",
    response_model=list[CurrentlyReadingItem],
    summary="Books currently being read",
)
async def currently_reading(
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await ReadingService(db).get_currently_reading(user.id, limit)
    return [
        CurrentlyReadingItem(
            book=BookSummary.model_validate(row["book"]),
            progress=ProgressOut.model_validate(row["progress"]),
            estimated_minutes_left=row["estimated_minutes_left"],
        )
        for row in rows
    ]


@router.post("/progress", response_model=SyncResult, summary="Update reading progress")
async def update_progress(
    payload: ProgressUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record progress for one book.

    Send ``base_version`` (the ``sync_version`` you last received) to get
    conflict detection; omit it to force last-write-wins.
    """
    service = ReadingService(db)
    row, outcome = await service.update_progress(
        user_id=user.id,
        book_id=payload.book_id,
        current_page=payload.current_page,
        percentage=payload.percentage,
        session_seconds=payload.session_seconds,
        device_type=payload.device_type,
        bookmarks=[b.model_dump(mode="json") for b in payload.bookmarks]
        if payload.bookmarks else None,
        notes=[n.model_dump(mode="json") for n in payload.notes] if payload.notes else None,
        base_version=payload.base_version,
    )
    return SyncResult(
        book_id=payload.book_id,
        status=outcome,
        sync_version=row.sync_version,
        progress=ProgressOut.model_validate(row),
    )


@router.get(
    "/progress/{book_id}", response_model=SyncResult, summary="Fetch progress"
)
async def get_progress(
    book_id: uuid.UUID,
    from_version: int | None = Query(
        None,
        description=(
            "Your last known sync_version. When the server has nothing newer, "
            "the response omits the payload and returns status='unchanged'."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await ReadingService(db).get_sync_data(user.id, book_id, from_version)
    return SyncResult(
        book_id=book_id,
        status=data["status"],
        sync_version=data["sync_version"],
        progress=ProgressOut(**data["progress"]) if data["progress"] else None,
    )


@router.post(
    "/batch-sync",
    response_model=BatchSyncResponse,
    summary="Sync up to 10 books at once",
)
async def batch_sync(
    payload: BatchSyncRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Apply several updates in one round trip.

    Each item succeeds or fails independently — check per-item ``status``
    rather than assuming an overall 200 means everything applied.
    """
    updates = []
    for item in payload.updates:
        updates.append({
            "book_id": item.book_id,
            "current_page": item.current_page,
            "percentage": item.percentage,
            "session_seconds": item.session_seconds,
            "device_type": item.device_type,
            "base_version": item.base_version,
            "bookmarks": [b.model_dump(mode="json") for b in item.bookmarks]
            if item.bookmarks else None,
            "notes": [n.model_dump(mode="json") for n in item.notes]
            if item.notes else None,
        })

    result = await ReadingService(db).batch_sync(user.id, updates)
    return BatchSyncResponse(
        results=[
            SyncResult(
                book_id=uuid.UUID(r["book_id"]),
                status=r["status"],
                sync_version=r["sync_version"],
                progress=ProgressOut(**r["progress"]) if r.get("progress") else None,
                message=r.get("message"),
            )
            for r in result["results"]
        ],
        applied=result["applied"],
        failed=result["failed"],
        server_time=result["server_time"],
    )


@router.get("/stats", response_model=ReadingStats, summary="Reading statistics")
async def reading_stats(
    period_days: int | None = Query(
        None, ge=1, le=365, description="Restrict to the last N days"
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return ReadingStats(**await ReadingService(db).get_reading_stats(user.id, period_days))


@router.get("/streak", summary="Reading streak")
async def reading_streak(
    tz_offset_minutes: int = Query(
        0,
        ge=-840,
        le=840,
        description=(
            "Your UTC offset in minutes, so day boundaries land in local time "
            "and a late-night session doesn't break the streak."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ReadingService(db).sync_read_streak(user.id, tz_offset_minutes)


@router.get("/sync-config", summary="Client sync settings")
async def sync_config():
    """Tell clients how to poll, rather than hardcoding it per platform."""
    return {
        "poll_interval_seconds": SYNC_INTERVAL_SECONDS,
        "max_batch_size": 10,
        "conflict_strategy": "version",
        "notes": (
            "Send base_version with every write. On status='conflict', re-read "
            "the server state, merge locally, then retry with the new version."
        ),
    }
