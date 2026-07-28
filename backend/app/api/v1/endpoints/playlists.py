"""Playlist endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, rate_limit
from app.core.database import get_db
from app.core.errors import ExternalServiceError, NotFoundError
from app.core.logging_config import get_logger
from app.models import Book, BookPlaylist, SavedPlaylist, User
from app.schemas.book import BookSummary
from app.schemas.common import Message
from app.schemas.playlist import (
    GeneratePlaylistRequest,
    PlaylistOut,
    PlaylistWithBook,
    PreviewOut,
    PreviewRequest,
    SavePlaylistRequest,
)
from app.services.music.music_service import MusicService

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/book/{book_id}", response_model=PlaylistWithBook, summary="Playlist for a book"
)
async def get_book_playlist(
    book_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a book's playlist, generating one on demand if absent."""
    book = await db.get(Book, book_id)
    if book is None:
        raise NotFoundError(f"No book with id {book_id}.", code="book_not_found")

    service = MusicService(db)
    playlist = await service.get_or_create_playlist(book_id)
    if playlist is None:
        raise ExternalServiceError(
            "Could not build a playlist for this book — no music source returned "
            "usable tracks. Try again later.",
            code="playlist_generation_failed",
        )

    match = await service.calculate_music_match(user.id, playlist)
    return PlaylistWithBook(
        playlist=PlaylistOut.model_validate(playlist),
        book=BookSummary.model_validate(book),
        user_match_score=match["score"],
    )


@router.post(
    "/generate",
    response_model=PlaylistOut,
    summary="Generate a playlist",
    # Generation fans out to several upstream searches, so it's the most
    # expensive route we expose.
    dependencies=[Depends(rate_limit(20, 3600, scope="playlist_generate"))],
)
async def generate_playlist(
    payload: GeneratePlaylistRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Book, payload.book_id) is None:
        raise NotFoundError(
            f"No book with id {payload.book_id}.", code="book_not_found"
        )

    playlist = await MusicService(db).get_or_create_playlist(
        payload.book_id,
        track_count=payload.track_count,
        force_source=payload.source,
        force_regenerate=payload.force_regenerate,
    )
    if playlist is None:
        raise ExternalServiceError(
            "No music source returned enough tracks for this book.",
            code="playlist_generation_failed",
        )
    return PlaylistOut.model_validate(playlist)


@router.post("/save", response_model=Message, summary="Save a playlist")
async def save_playlist(
    payload: SavePlaylistRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(BookPlaylist, payload.playlist_id) is None:
        raise NotFoundError(
            f"No playlist with id {payload.playlist_id}.", code="playlist_not_found"
        )

    # Saving twice is a no-op rather than an error — the client shouldn't have
    # to track whether it already saved.
    await db.execute(
        pg_insert(SavedPlaylist)
        .values(user_id=user.id, playlist_id=payload.playlist_id)
        .on_conflict_do_nothing(constraint="uq_saved_playlist")
    )
    await db.commit()
    return Message(message="Playlist saved to your library.")


@router.delete(
    "/save/{playlist_id}", response_model=Message, summary="Unsave a playlist"
)
async def unsave_playlist(
    playlist_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(SavedPlaylist).where(
            SavedPlaylist.user_id == user.id,
            SavedPlaylist.playlist_id == playlist_id,
        )
    )
    if row is None:
        raise NotFoundError("That playlist isn't in your library.", code="not_saved")
    await db.delete(row)
    await db.commit()
    return Message(message="Playlist removed from your library.")


@router.get(
    "/user", response_model=list[PlaylistWithBook], summary="Your saved playlists"
)
async def user_playlists(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(BookPlaylist, Book)
            .join(SavedPlaylist, SavedPlaylist.playlist_id == BookPlaylist.id)
            .join(Book, Book.id == BookPlaylist.book_id)
            .where(SavedPlaylist.user_id == user.id)
            .order_by(desc(SavedPlaylist.saved_at))
            .limit(limit)
            .offset(offset)
        )
    ).all()

    service = MusicService(db)
    out = []
    for playlist, book in rows:
        match = await service.calculate_music_match(user.id, playlist)
        out.append(
            PlaylistWithBook(
                playlist=PlaylistOut.model_validate(playlist),
                book=BookSummary.model_validate(book),
                user_match_score=match["score"],
            )
        )
    return out


@router.post(
    "/preview", response_model=list[PreviewOut], summary="Resolve 30s previews"
)
async def preview_tracks(
    payload: PreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Look up preview clips for tracks.

    Spotify only publishes previews for a subset of tracks and only in some
    markets, so expect `available: false` frequently — it's not an error.
    """
    results = await MusicService(db).get_previews(payload.track_ids, payload.source)
    return [PreviewOut(**r) for r in results]
