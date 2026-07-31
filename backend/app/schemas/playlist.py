"""Playlist schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.book import BookSummary
from app.schemas.common import ORMModel

# "deezer" means Deezer-curated and YouTube-resolved: the playlist was built
# from Deezer's public playlist search, but each track plays from YouTube
# Music. So a playlist carries source="deezer" while its tracks carry
# "youtube_music".
PlaylistSource = Literal["deezer", "spotify", "youtube_music", "custom"]


class Track(BaseModel):
    id: str
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    # Null for every YouTube Music track — it has no preview-clip concept — and
    # for most Spotify tracks. Clients must handle this being null.
    preview_url: str | None = None
    external_url: str | None = None
    artwork_url: str | None = None
    source: PlaylistSource = "youtube_music"


class PlaylistOut(ORMModel):
    id: uuid.UUID
    book_id: uuid.UUID
    playlist_name: str
    description: str | None = None
    source: PlaylistSource
    source_playlist_id: str | None = None
    playlist_url: str | None = None
    tracks: list[Track] = Field(default_factory=list)
    mood_match_score: float | None = None
    genre_match_score: float | None = None
    created_at: datetime | None = None


class PlaylistWithBook(BaseModel):
    playlist: PlaylistOut
    book: BookSummary
    user_match_score: float | None = Field(
        None, description="Fit against this user's music taste, 0-100"
    )


class GeneratePlaylistRequest(BaseModel):
    book_id: uuid.UUID
    source: PlaylistSource | None = Field(
        None,
        description=(
            "Force a source; defaults to Deezer curation resolved through "
            "YouTube Music, falling back to YouTube Music keyword search"
        ),
    )
    track_count: int = Field(20, ge=5, le=50)
    force_regenerate: bool = False


class SavePlaylistRequest(BaseModel):
    playlist_id: uuid.UUID


class PreviewRequest(BaseModel):
    track_ids: list[str] = Field(..., min_length=1, max_length=50)
    source: PlaylistSource = "youtube_music"


class PreviewOut(BaseModel):
    track_id: str
    preview_url: str | None
    available: bool
    reason: str | None = None
