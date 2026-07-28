"""Reading progress tracking and cross-device sync.

Sync model: optimistic concurrency on a monotonically increasing
``sync_version`` per (user, book). A client sends the version it last saw; if
the server has moved on, the write is rejected as a conflict and the client
gets the current state back so it can merge. That's the difference between
"two devices stay consistent" and "whichever device syncs last wins and the
other silently loses a chapter of highlights".

Clients poll every 30s (see ``SYNC_INTERVAL_SECONDS``).
"""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging_config import get_logger
from app.core.redis_client import cache_delete, cache_get, cache_set
from app.models import Book, ReadingProgress, UserBookInteraction

logger = get_logger(__name__)

SYNC_INTERVAL_SECONDS = 30
STATS_CACHE_TTL = 3600
_PROGRESS_PREFIX = "progress:v1:"
_STATS_PREFIX = "stats:v1:"

# Used to convert pages -> minutes when a user has no measured speed yet.
DEFAULT_PAGES_PER_MINUTE = 0.7


class ReadingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- Progress ---------------------------------------------------------

    async def _get_row(
        self, user_id: uuid.UUID, book_id: uuid.UUID
    ) -> ReadingProgress | None:
        return await self.session.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == user_id,
                ReadingProgress.book_id == book_id,
            )
        )

    async def update_progress(
        self,
        user_id: uuid.UUID,
        book_id: uuid.UUID,
        current_page: int | None = None,
        percentage: float | None = None,
        session_seconds: int = 0,
        device_type: str | None = None,
        bookmarks: list[dict[str, Any]] | None = None,
        notes: list[dict[str, Any]] | None = None,
        base_version: int | None = None,
    ) -> tuple[ReadingProgress, str]:
        """Apply a progress update. Returns ``(row, outcome)``."""
        book = await self.session.get(Book, book_id)
        if book is None:
            raise NotFoundError(f"Book {book_id} does not exist.", code="book_not_found")

        total_pages = book.page_count
        if current_page is not None and total_pages and current_page > total_pages:
            raise ValidationError(
                f"Page {current_page} is beyond this book's {total_pages} pages.",
                code="page_out_of_range",
                details={"current_page": current_page, "page_count": total_pages},
            )

        row = await self._get_row(user_id, book_id)

        if row is not None and base_version is not None and base_version < row.sync_version:
            # Another device already wrote past this client's snapshot.
            raise ConflictError(
                "This book was updated on another device. Merge and retry.",
                code="sync_conflict",
                details={
                    "server_version": row.sync_version,
                    "your_version": base_version,
                    "server_page": row.current_page,
                    "server_percentage": float(row.percentage),
                },
            )

        # Derive whichever of page/percentage wasn't supplied.
        if percentage is None and current_page is not None and total_pages:
            percentage = round(min(100.0, current_page / total_pages * 100), 2)
        if current_page is None and percentage is not None and total_pages:
            current_page = int(round(total_pages * percentage / 100))

        now = datetime.now(UTC)

        if row is None:
            row = ReadingProgress(
                user_id=user_id,
                book_id=book_id,
                current_page=current_page or 0,
                percentage=percentage or 0.0,
                total_time_spent=session_seconds,
                device_type=device_type,
                last_read_at=now,
                sync_version=1,
                bookmarks=bookmarks or [],
                notes=notes or [],
            )
            row.reading_speed = self._speed(row.current_page, session_seconds)
            self.session.add(row)
            outcome = "applied"
        else:
            pages_delta = max(0, (current_page or 0) - row.current_page)

            # A rewind is legitimate (re-reading), so accept it — but don't let
            # it inflate totals or corrupt the speed estimate.
            if current_page is not None:
                row.current_page = current_page
            if percentage is not None:
                row.percentage = percentage

            row.total_time_spent = (row.total_time_spent or 0) + session_seconds
            if session_seconds > 0 and pages_delta > 0:
                row.reading_speed = self._blend_speed(
                    row.reading_speed, pages_delta, session_seconds
                )
            if device_type:
                row.device_type = device_type
            if bookmarks is not None:
                row.bookmarks = self._merge_annotations(row.bookmarks, bookmarks, "label")
            if notes is not None:
                row.notes = self._merge_annotations(row.notes, notes, "text")

            row.last_read_at = now
            row.sync_version += 1
            outcome = "applied"

        await self._sync_interaction(user_id, book_id, row, session_seconds, now)
        await self.session.commit()
        await self.session.refresh(row)

        await cache_delete(
            f"{_PROGRESS_PREFIX}{user_id}:{book_id}", f"{_STATS_PREFIX}{user_id}"
        )
        await cache_set(
            f"{_PROGRESS_PREFIX}{user_id}:{book_id}",
            self.serialize(row),
            ttl=SYNC_INTERVAL_SECONDS * 4,
        )
        return row, outcome

    @staticmethod
    def _speed(pages: int, seconds: int) -> float | None:
        if not seconds or pages <= 0:
            return None
        speed = pages / (seconds / 60.0)
        # Clamp to something physically plausible — a stray 1-second session
        # would otherwise record 300 pages/minute and poison every estimate.
        return round(min(20.0, max(0.05, speed)), 2)

    @classmethod
    def _blend_speed(
        cls, existing: float | None, pages: int, seconds: int
    ) -> float | None:
        """Exponential moving average, so one odd session can't dominate."""
        session_speed = cls._speed(pages, seconds)
        if session_speed is None:
            return existing
        if existing is None:
            return session_speed
        return round(float(existing) * 0.7 + session_speed * 0.3, 2)

    @staticmethod
    def _merge_annotations(
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        content_key: str,
    ) -> list[dict[str, Any]]:
        """Union bookmarks/notes by (page, content).

        Merging rather than replacing means a device that syncs a stale list
        can't delete annotations another device just added.
        """
        merged = {(item.get("page"), item.get(content_key)): item for item in existing or []}
        for item in incoming or []:
            payload = dict(item)
            payload.setdefault("created_at", datetime.now(UTC).isoformat())
            merged[(payload.get("page"), payload.get(content_key))] = payload
        return sorted(merged.values(), key=lambda i: i.get("page") or 0)

    async def _sync_interaction(
        self,
        user_id: uuid.UUID,
        book_id: uuid.UUID,
        progress: ReadingProgress,
        session_seconds: int,
        now: datetime,
    ) -> None:
        """Keep the library row in step with reading activity."""
        interaction = await self.session.scalar(
            select(UserBookInteraction).where(
                UserBookInteraction.user_id == user_id,
                UserBookInteraction.book_id == book_id,
            )
        )
        if interaction is None:
            interaction = UserBookInteraction(
                user_id=user_id, book_id=book_id, status="currently_reading",
                started_reading_at=now,
            )
            self.session.add(interaction)

        interaction.interaction_count = (interaction.interaction_count or 0) + 1
        interaction.total_time_spent = (interaction.total_time_spent or 0) + session_seconds
        interaction.last_interaction_at = now

        if interaction.status in (None, "want_to_read"):
            interaction.status = "currently_reading"
            interaction.started_reading_at = interaction.started_reading_at or now

        # 98% rather than 100% — back matter, acknowledgements and indices mean
        # readers rarely register the final page.
        if float(progress.percentage) >= 98 and interaction.status != "read":
            interaction.status = "read"
            interaction.finished_reading_at = now

    @staticmethod
    def serialize(row: ReadingProgress) -> dict[str, Any]:
        return {
            "book_id": str(row.book_id),
            "current_page": row.current_page,
            "percentage": float(row.percentage or 0),
            "reading_speed": float(row.reading_speed) if row.reading_speed else None,
            "total_time_spent": row.total_time_spent or 0,
            "last_read_at": row.last_read_at.isoformat() if row.last_read_at else None,
            "device_type": row.device_type,
            "sync_version": row.sync_version,
            "bookmarks": row.bookmarks or [],
            "notes": row.notes or [],
        }

    # -- Sync -------------------------------------------------------------

    async def get_sync_data(
        self,
        user_id: uuid.UUID,
        book_id: uuid.UUID,
        from_version: int | None = None,
    ) -> dict[str, Any]:
        """Fetch progress, returning a delta when the client is up to date."""
        cache_key = f"{_PROGRESS_PREFIX}{user_id}:{book_id}"
        payload = await cache_get(cache_key)

        if payload is None:
            row = await self._get_row(user_id, book_id)
            if row is None:
                raise NotFoundError(
                    "No reading progress recorded for this book.",
                    code="progress_not_found",
                )
            payload = self.serialize(row)
            await cache_set(cache_key, payload, ttl=SYNC_INTERVAL_SECONDS * 4)

        if from_version is not None and payload["sync_version"] <= from_version:
            # Nothing new — send the version only. On a 30s poll across many
            # clients this is the difference between a trickle of bytes and
            # re-sending every note on every tick.
            return {
                "book_id": str(book_id),
                "status": "unchanged",
                "sync_version": payload["sync_version"],
                "progress": None,
            }

        return {
            "book_id": str(book_id),
            "status": "applied",
            "sync_version": payload["sync_version"],
            "progress": payload,
        }

    async def batch_sync(
        self, user_id: uuid.UUID, updates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Apply up to 10 updates, isolating failures per item.

        Partial success is the point: one book hitting a conflict must not
        discard the other nine updates the client batched with it.
        """
        if len(updates) > 10:
            raise ValidationError(
                "A batch may contain at most 10 updates.", code="batch_too_large"
            )

        results: list[dict[str, Any]] = []
        applied = failed = 0

        for update in updates:
            book_id = update["book_id"]
            try:
                row, outcome = await self.update_progress(
                    user_id=user_id,
                    book_id=book_id,
                    current_page=update.get("current_page"),
                    percentage=update.get("percentage"),
                    session_seconds=update.get("session_seconds", 0),
                    device_type=update.get("device_type"),
                    bookmarks=update.get("bookmarks"),
                    notes=update.get("notes"),
                    base_version=update.get("base_version"),
                )
                results.append({
                    "book_id": str(book_id), "status": outcome,
                    "sync_version": row.sync_version, "progress": self.serialize(row),
                })
                applied += 1
            except ConflictError as exc:
                results.append({
                    "book_id": str(book_id), "status": "conflict",
                    "sync_version": exc.details.get("server_version", 0),
                    "progress": None, "message": exc.message,
                })
                failed += 1
            except Exception as exc:
                logger.warning("Batch sync item %s failed: %s", book_id, exc)
                await self.session.rollback()
                results.append({
                    "book_id": str(book_id), "status": "error",
                    "sync_version": 0, "progress": None, "message": str(exc),
                })
                failed += 1

        return {
            "results": results,
            "applied": applied,
            "failed": failed,
            "server_time": datetime.now(UTC),
        }

    # -- Streaks ----------------------------------------------------------

    async def sync_read_streak(
        self, user_id: uuid.UUID, tz_offset_minutes: int = 0
    ) -> dict[str, Any]:
        """Compute consecutive-day reading streaks.

        ``tz_offset_minutes`` shifts day boundaries into the user's local time —
        without it, someone reading at 11pm UTC-5 has their session counted as
        the next day and the streak breaks for no visible reason.
        """
        since = datetime.now(UTC) - timedelta(days=400)
        rows = await self.session.scalars(
            select(ReadingProgress.last_read_at).where(
                ReadingProgress.user_id == user_id,
                ReadingProgress.last_read_at >= since,
            )
        )

        offset = timedelta(minutes=tz_offset_minutes)
        days = sorted(
            {(ts + offset).date() for ts in rows.all() if ts is not None}, reverse=True
        )
        if not days:
            return {"current_streak": 0, "longest_streak": 0, "milestone": None}

        today = (datetime.now(UTC) + offset).date()
        current = 0
        # Reading yesterday still counts — the streak only breaks once a full
        # day has been missed.
        if days[0] in (today, today - timedelta(days=1)):
            current = 1
            for prev, nxt in zip(days, days[1:], strict=False):
                if (prev - nxt).days == 1:
                    current += 1
                else:
                    break

        longest = run = 1
        for prev, nxt in zip(days, days[1:], strict=False):
            run = run + 1 if (prev - nxt).days == 1 else 1
            longest = max(longest, run)

        milestone = next((m for m in (365, 90, 30, 7) if current == m), None)
        return {
            "current_streak": current,
            "longest_streak": max(longest, current),
            "milestone": milestone,
            "active_days_last_30": sum(1 for d in days if (today - d).days < 30),
        }

    # -- Stats ------------------------------------------------------------

    async def get_reading_stats(
        self, user_id: uuid.UUID, period_days: int | None = None
    ) -> dict[str, Any]:
        cache_key = f"{_STATS_PREFIX}{user_id}:{period_days or 'all'}"
        if cached := await cache_get(cache_key):
            return cached

        since = (
            datetime.now(UTC) - timedelta(days=period_days)
            if period_days else None
        )

        interaction_q = select(
            UserBookInteraction.status,
            UserBookInteraction.total_time_spent,
            UserBookInteraction.book_id,
        ).where(UserBookInteraction.user_id == user_id)
        if since is not None:
            interaction_q = interaction_q.where(
                UserBookInteraction.last_interaction_at >= since
            )
        interactions = (await self.session.execute(interaction_q)).all()

        progress_q = select(
            ReadingProgress.current_page,
            ReadingProgress.percentage,
            ReadingProgress.reading_speed,
            ReadingProgress.total_time_spent,
            ReadingProgress.last_read_at,
            ReadingProgress.book_id,
        ).where(ReadingProgress.user_id == user_id)
        if since is not None:
            progress_q = progress_q.where(ReadingProgress.last_read_at >= since)
        progress_rows = (await self.session.execute(progress_q)).all()

        books_read = sum(1 for status, _, _ in interactions if status == "read")
        in_progress = sum(1 for status, _, _ in interactions if status == "currently_reading")

        total_pages = sum(row[0] or 0 for row in progress_rows)
        total_seconds = sum(row[3] or 0 for row in progress_rows)
        speeds = [float(row[2]) for row in progress_rows if row[2]]
        percentages = [float(row[1] or 0) for row in progress_rows]

        # Favourite genres weighted by time spent, not book count — twenty
        # minutes on an abandoned book shouldn't outrank a finished one.
        genre_seconds: Counter = Counter()
        if progress_rows:
            book_ids = [row[5] for row in progress_rows]
            genre_rows = (
                await self.session.execute(
                    select(Book.id, Book.genres).where(Book.id.in_(book_ids))
                )
            ).all()
            genres_by_book = {bid: (genres or []) for bid, genres in genre_rows}
            for row in progress_rows:
                for genre in genres_by_book.get(row[5], []):
                    genre_seconds[genre] += row[3] or 0

        hour_counts = Counter(
            row[4].hour for row in progress_rows if row[4] is not None
        )

        streak = await self.sync_read_streak(user_id)

        stats = {
            "total_books_read": books_read,
            "total_books_in_progress": in_progress,
            "total_pages_read": total_pages,
            "total_reading_seconds": total_seconds,
            "average_reading_speed": round(sum(speeds) / len(speeds), 2) if speeds else None,
            "average_completion_rate": (
                round(sum(percentages) / len(percentages), 2) if percentages else 0.0
            ),
            "current_streak_days": streak["current_streak"],
            "longest_streak_days": streak["longest_streak"],
            "favorite_genres": [
                {"genre": genre, "seconds": seconds}
                for genre, seconds in genre_seconds.most_common(5)
            ],
            "most_productive_hour": hour_counts.most_common(1)[0][0] if hour_counts else None,
            "period_days": period_days,
        }
        await cache_set(cache_key, stats, ttl=STATS_CACHE_TTL)
        return stats

    async def get_currently_reading(
        self, user_id: uuid.UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(Book, ReadingProgress)
                .join(ReadingProgress, ReadingProgress.book_id == Book.id)
                .join(
                    UserBookInteraction,
                    (UserBookInteraction.book_id == Book.id)
                    & (UserBookInteraction.user_id == user_id),
                )
                .where(
                    ReadingProgress.user_id == user_id,
                    UserBookInteraction.status == "currently_reading",
                )
                .order_by(ReadingProgress.last_read_at.desc())
                .limit(limit)
            )
        ).all()

        out: list[dict[str, Any]] = []
        for book, progress in rows:
            remaining_minutes = None
            if book.page_count:
                pages_left = max(0, book.page_count - (progress.current_page or 0))
                speed = float(progress.reading_speed or 0) or DEFAULT_PAGES_PER_MINUTE
                remaining_minutes = int(pages_left / speed) if speed else None
            out.append({
                "book": book,
                "progress": progress,
                "estimated_minutes_left": remaining_minutes,
            })
        return out
