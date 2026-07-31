"""Playlist generation: Deezer curates, YouTube Music plays.

Neither of the two primary sources needs an API key, so playlists work on a
fresh checkout with no credentials configured at all.

Sources, in the order they are tried:

1. **Deezer** — mood/genre tags resolved against its public playlist search
   give the track *names*. This is the discovery layer and the one that decides
   whether a playlist actually fits the book. It returns no playable media.
2. **YouTube Music** — resolves each curated name to a playable track, and is
   also the standalone fallback (keyword search) when Deezer is unreachable
   or returns too little.
3. **Spotify** — retained but disabled unless credentials are set. Two things
   broke it as a primary: apps created after 2024-11-27 get 403 from
   `/recommendations`, `/audio-features` and related-artists, and search now
   requires the app owner to hold an active Premium subscription. If you have
   a pre-cutoff app and Premium, setting the credentials re-enables it.

A playlist built via path 1+2 is stored with ``source="deezer"`` — that is what
curated it — while its individual tracks carry ``source="youtube_music"``,
which is what plays them.

Note on latency: resolving curated names one-by-one costs one YouTube Music
search each, and that limiter is deliberately conservative, so a cold 20-track
playlist takes tens of seconds. This is why `tasks.generate_playlists` pre-builds
them nightly and results are persisted; the request path is almost always a
cache or DB hit.

The Spotify and YouTube clients are synchronous libraries, so their calls are
dispatched through ``asyncio.to_thread`` to keep the event loop free.
"""

import asyncio
import itertools
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set
from app.models import Book, BookPlaylist, User
from app.services.music.deezer_client import DeezerClient
from app.services.music.genre_mapping import (
    DEFAULT_DESCRIPTION,
    DEFAULT_NAME_TEMPLATE,
    DESCRIPTION_TEMPLATES,
    PLAYLIST_NAME_TEMPLATES,
    audio_feature_targets,
    music_genres_for_book,
    music_tags_for_book,
    search_terms_for_book,
)
from app.utils.rate_limiter import spotify_limiter, youtube_limiter

logger = get_logger(__name__)

SEARCH_CACHE_TTL = 60 * 60 * 24 * 3
PLAYLIST_CACHE_TTL = 60 * 60 * 24 * 7
_SEARCH_PREFIX = "music:search:v1:"
_PLAYLIST_PREFIX = "music:playlist:v1:"

# How many name-resolutions run at once. The YouTube limiter is the real
# throttle; this just stops a burst of coroutines all queueing on it.
_RESOLVE_CONCURRENCY = 5
# Resolve a few more than needed — a small share of curated names have no
# YouTube Music match. Kept tight because each miss still costs a search.
_RESOLVE_OVERFETCH = 6


class MusicService:
    """Wraps Spotify + YouTube Music behind one playlist-shaped interface."""

    _spotify = None
    _spotify_failed = False       # set after auth failure; stops retry storms
    _youtube = None
    _youtube_failed = False

    def __init__(self, session: AsyncSession | None = None):
        self.session = session

    # -- Client setup -----------------------------------------------------

    @classmethod
    def initialize_clients(cls) -> dict[str, bool]:
        """Build the clients. Safe to call repeatedly; caches the result.

        Deezer needs no client object and no credentials — it's a keyless REST
        API — so it reports reachability rather than a connection result.
        """
        return {
            "deezer": DeezerClient.is_configured(),
            "youtube_music": cls._init_youtube(),
            "spotify": cls._init_spotify(),
        }

    @classmethod
    def _init_spotify(cls) -> bool:
        if cls._spotify is not None:
            return True
        if cls._spotify_failed:
            return False
        if not (settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CLIENT_SECRET):
            logger.warning(
                "SPOTIFY_CLIENT_ID/SECRET not set — Spotify disabled, using "
                "YouTube Music only. Create an app at "
                "https://developer.spotify.com/dashboard"
            )
            cls._spotify_failed = True
            return False
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            # Client Credentials: app-level access, no user login, no redirect
            # URI. Sufficient for search and metadata; it cannot read a user's
            # listening history or create playlists in their account.
            auth = SpotifyClientCredentials(
                client_id=settings.SPOTIFY_CLIENT_ID,
                client_secret=settings.SPOTIFY_CLIENT_SECRET,
            )
            client = spotipy.Spotify(
                auth_manager=auth, requests_timeout=10, retries=2,
                status_forcelist=(429, 500, 502, 503, 504),
            )
            client.search(q="test", type="track", limit=1)  # verify credentials
            cls._spotify = client
            logger.info("Spotify client ready")
            return True
        except Exception as exc:
            logger.error("Spotify init failed: %s", exc)
            cls._spotify_failed = True
            return False

    @classmethod
    def _init_youtube(cls) -> bool:
        if cls._youtube is not None:
            return True
        if cls._youtube_failed:
            return False
        try:
            from ytmusicapi import YTMusic

            # No auth file: unauthenticated YTMusic supports search, which is
            # all the fallback needs. Passing an oauth file would let us build
            # real playlists in a Google account.
            cls._youtube = YTMusic()
            logger.info("YouTube Music client ready (unauthenticated)")
            return True
        except Exception as exc:
            logger.warning("YouTube Music init failed: %s", exc)
            cls._youtube_failed = True
            return False

    @classmethod
    def health(cls) -> dict[str, bool]:
        return {
            "deezer": DeezerClient.is_configured(),
            "youtube_music": cls._youtube is not None,
            "spotify": cls._spotify is not None,
        }

    # -- Spotify ----------------------------------------------------------

    async def search_spotify_tracks(
        self, query: str, limit: int = 20, market: str = "US"
    ) -> list[dict[str, Any]]:
        if not self._init_spotify():
            return []

        cache_key = f"{_SEARCH_PREFIX}sp:{query}:{limit}"
        if cached := await cache_get(cache_key):
            return cached

        await spotify_limiter.acquire()
        try:
            payload = await asyncio.to_thread(
                self._spotify.search,
                q=query, type="track", limit=min(limit, 50), market=market,
            )
        except Exception as exc:
            logger.warning("Spotify search %r failed: %s", query, exc)
            return []

        tracks = [
            self._parse_spotify_track(item)
            for item in (payload.get("tracks", {}).get("items") or [])
        ]
        tracks = [t for t in tracks if t]
        await cache_set(cache_key, tracks, ttl=SEARCH_CACHE_TTL)
        return tracks

    @staticmethod
    def _parse_spotify_track(item: dict[str, Any]) -> dict[str, Any] | None:
        if not item or not item.get("id"):
            return None
        album = item.get("album") or {}
        images = album.get("images") or []
        return {
            "id": item["id"],
            "title": item.get("name", "Unknown"),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists") or []),
            "album": album.get("name"),
            "duration_ms": item.get("duration_ms"),
            # Null for most tracks these days — Spotify has been withdrawing
            # preview URLs. Clients must handle it.
            "preview_url": item.get("preview_url"),
            "external_url": (item.get("external_urls") or {}).get("spotify"),
            "artwork_url": images[0]["url"] if images else None,
            "source": "spotify",
        }

    async def get_spotify_recommendations(
        self,
        seed_genres: Sequence[str],
        audio_targets: dict[str, float] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Try Spotify's recommendations endpoint.

        Returns [] on the 403/404 that post-2024 apps get — the caller treats
        that as "use search instead", not as an error.
        """
        if not self._init_spotify() or not seed_genres:
            return []

        await spotify_limiter.acquire()
        params: dict[str, Any] = {
            "seed_genres": list(seed_genres)[:5],  # hard API cap of 5 seeds
            "limit": min(limit, 100),
        }
        params.update(audio_targets or {})

        try:
            payload = await asyncio.to_thread(self._spotify.recommendations, **params)
        except Exception as exc:
            logger.info(
                "Spotify recommendations unavailable (expected for apps created "
                "after 2024-11-27): %s — falling back to search", exc
            )
            return []

        tracks = [self._parse_spotify_track(t) for t in payload.get("tracks") or []]
        return [t for t in tracks if t]

    # -- Deezer (discovery) -----------------------------------------------

    async def get_curated_tracks(
        self, tags: Sequence[str], limit: int = 20
    ) -> list[dict[str, Any]]:
        """Pull tracks for the given curation tags and make them playable.

        Deezer returns names only, so each survivor costs one YouTube Music
        search. Returns playable track dicts in the usual shape.
        """
        if not DeezerClient.is_configured() or not tags:
            return []

        # Ask each tag for more than its even share so that a thin tag doesn't
        # starve the playlist — the interleave below decides the final mix.
        per_tag = max(10, (limit // max(1, len(tags))) * 3)
        pools = await asyncio.gather(
            *(DeezerClient.tag_top_tracks(tag, per_tag) for tag in tags)
        )

        pairs = self._interleave_pairs(pools, limit + _RESOLVE_OVERFETCH)
        if not pairs:
            logger.info("Deezer returned no tracks for tags %s", list(tags))
            return []

        semaphore = asyncio.Semaphore(_RESOLVE_CONCURRENCY)
        resolved = await asyncio.gather(
            *(self._resolve_pair(pair, semaphore) for pair in pairs)
        )
        tracks = [track for track in resolved if track]

        logger.info(
            "Deezer: %d names from tags %s, %d resolved on YouTube Music",
            len(pairs), list(tags), len(tracks),
        )
        return tracks[:limit]

    @staticmethod
    def _interleave_pairs(
        pools: Sequence[Sequence[dict[str, str]]], cap: int
    ) -> list[dict[str, str]]:
        """Round-robin the per-tag name lists, deduped case-insensitively.

        Same reasoning as `_select_tracks`: taking the first N from one tag
        gives a single-flavour playlist, which defeats blending moods.
        """
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for row in itertools.zip_longest(*pools):
            for pair in row:
                if not pair:
                    continue
                key = (pair["artist"].lower(), pair["title"].lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append(pair)
                if len(out) >= cap:
                    return out
        return out

    async def _resolve_pair(
        self, pair: dict[str, str], semaphore: asyncio.Semaphore
    ) -> dict[str, Any] | None:
        """Find the YouTube Music track for one curated artist/title pair.

        `get_youtube_tracks` supplies both the cache and the rate limiting, so
        a name resolved for one book is free for every later book.
        """
        async with semaphore:
            query = f"{pair['artist']} {pair['title']}"
            results = await self.get_youtube_tracks(query, limit=1)

        if not results:
            return None

        track = dict(results[0])
        # Keep Deezer's spelling alongside YouTube's — the two disagree often
        # enough (remasters, live versions) that it's worth being able to tell
        # what was actually asked for.
        track["curated_title"] = pair["title"]
        track["curated_artist"] = pair["artist"]
        return track

    # -- YouTube Music ----------------------------------------------------

    async def get_youtube_tracks(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not self._init_youtube():
            return []

        cache_key = f"{_SEARCH_PREFIX}yt:{query}:{limit}"
        if cached := await cache_get(cache_key):
            return cached

        await youtube_limiter.acquire()
        try:
            results = await asyncio.to_thread(
                self._youtube.search, query, filter="songs", limit=min(limit, 40)
            )
        except Exception as exc:
            logger.warning("YouTube Music search %r failed: %s", query, exc)
            return []

        tracks = []
        for item in results or []:
            video_id = item.get("videoId")
            if not video_id:
                continue
            thumbnails = item.get("thumbnails") or []
            duration = item.get("duration_seconds")
            tracks.append({
                "id": video_id,
                "title": item.get("title", "Unknown"),
                "artist": ", ".join(a.get("name", "") for a in item.get("artists") or []),
                "album": (item.get("album") or {}).get("name"),
                "duration_ms": duration * 1000 if duration else None,
                "preview_url": None,  # YT Music has no preview-clip concept
                "external_url": f"https://music.youtube.com/watch?v={video_id}",
                "artwork_url": thumbnails[-1]["url"] if thumbnails else None,
                "source": "youtube_music",
            })

        await cache_set(cache_key, tracks, ttl=SEARCH_CACHE_TTL)
        return tracks

    # -- Playlist assembly ------------------------------------------------

    async def generate_playlist_metadata(self, book: Book) -> dict[str, str]:
        primary = (book.genres or ["fiction"])[0]
        name = PLAYLIST_NAME_TEMPLATES.get(primary, DEFAULT_NAME_TEMPLATE).format(
            title=book.title[:60], author=book.author
        )
        description = DESCRIPTION_TEMPLATES.get(primary, DEFAULT_DESCRIPTION).format(
            title=book.title, author=book.author
        )
        return {"name": name[:120], "description": description[:300]}

    @staticmethod
    def _select_tracks(
        pools: list[list[dict[str, Any]]], target: int
    ) -> list[dict[str, Any]]:
        """Interleave the per-query result pools, deduping by id and capping
        any one artist at two tracks.

        Round-robin rather than concatenation: taking the first N from one
        query gives a playlist that's all one sound, which defeats the point
        of searching several mood terms.
        """
        selected: list[dict[str, Any]] = []
        seen_ids: set = set()
        artist_counts: dict[str, int] = {}
        cursor = 0

        while len(selected) < target and any(cursor < len(p) for p in pools):
            for pool in pools:
                if cursor >= len(pool):
                    continue
                track = pool[cursor]
                artist = (track.get("artist") or "").lower()
                if track["id"] in seen_ids or artist_counts.get(artist, 0) >= 2:
                    continue
                selected.append(track)
                seen_ids.add(track["id"])
                artist_counts[artist] = artist_counts.get(artist, 0) + 1
                if len(selected) >= target:
                    break
            cursor += 1

        return selected

    async def generate_book_playlist(
        self,
        book: Book,
        track_count: int = 20,
        force_source: str | None = None,
    ) -> dict[str, Any] | None:
        """Build a playlist for a book. Returns ``None`` if no source yielded
        enough tracks."""
        genres = book.genres or []
        moods = book.moods or {}
        queries = search_terms_for_book(genres, moods)
        metadata = await self.generate_playlist_metadata(book)

        pools: list[list[dict[str, Any]]] = []
        source = "deezer"

        # 1. Deezer curation, resolved through YouTube Music.
        if force_source in (None, "deezer"):
            tags = music_tags_for_book(genres, moods)
            if curated := await self.get_curated_tracks(tags, limit=track_count):
                pools.append(curated)

        # 2. Spotify, only if it was explicitly asked for or credentials exist.
        if force_source == "spotify" or (force_source is None and not pools):
            targets = audio_feature_targets(moods)
            seeds = music_genres_for_book(genres, limit=5)
            if recommended := await self.get_spotify_recommendations(
                seeds, targets, limit=track_count
            ):
                pools.append(recommended)
                source = "spotify"

            for query in queries[:4]:
                if tracks := await self.search_spotify_tracks(query, limit=15):
                    pools.append(tracks)
                    source = "spotify"

        # 3. YouTube Music keyword search — the last resort, and the only path
        #    that needs no credentials of any kind.
        available = sum(len(p) for p in pools)
        if available < track_count // 2 and force_source not in ("deezer", "spotify"):
            logger.info(
                "Curated sources returned %d tracks for %r — falling back to "
                "YouTube Music keyword search",
                available, book.title,
            )
            yt_pools = []
            for query in queries[:4]:
                if tracks := await self.get_youtube_tracks(query, limit=15):
                    yt_pools.append(tracks)
            if sum(len(p) for p in yt_pools) > available:
                pools, source = yt_pools, "youtube_music"

        tracks = self._select_tracks(pools, track_count)
        if not tracks:
            logger.warning("No tracks found for %r", book.title)
            return None

        return {
            "playlist_name": metadata["name"],
            "description": metadata["description"],
            "source": source,
            "source_playlist_id": None,
            "playlist_url": None,
            "tracks": tracks,
            "mood_match_score": self._mood_match_score(moods, len(tracks), track_count),
            "genre_match_score": self._genre_match_score(genres, len(tracks), track_count),
        }

    @staticmethod
    def _mood_match_score(moods: dict[str, float], found: int, target: int) -> float:
        """Confidence that the playlist reflects the book's mood.

        Derived from how much mood signal the book had and how completely we
        filled the playlist — we can't measure the audio directly without the
        deprecated audio-features endpoint.
        """
        if not moods:
            return 50.0
        strength = sum(moods.values()) / max(1, len(moods))
        completeness = min(1.0, found / max(1, target))
        return round(min(100.0, 40 + strength * 40 + completeness * 20), 2)

    @staticmethod
    def _genre_match_score(genres: list[str], found: int, target: int) -> float:
        if not genres:
            return 50.0
        mapped = len(music_genres_for_book(genres, limit=5))
        completeness = min(1.0, found / max(1, target))
        return round(min(100.0, 40 + mapped * 8 + completeness * 25), 2)

    # -- Persistence ------------------------------------------------------

    async def get_or_create_playlist(
        self,
        book_id: uuid.UUID,
        track_count: int = 20,
        force_source: str | None = None,
        force_regenerate: bool = False,
    ) -> BookPlaylist | None:
        if self.session is None:
            raise RuntimeError("MusicService needs a session for persistence")

        if not force_regenerate:
            existing = await self.session.scalar(
                select(BookPlaylist)
                .where(BookPlaylist.book_id == book_id)
                .order_by(BookPlaylist.created_at.desc())
            )
            if existing:
                return existing

        book = await self.session.get(Book, book_id)
        if book is None:
            return None

        generated = await self.generate_book_playlist(book, track_count, force_source)
        if generated is None:
            return None

        # (book_id, source) is unique — regeneration replaces in place.
        await self.session.execute(
            pg_insert(BookPlaylist)
            .values(book_id=book_id, **generated)
            .on_conflict_do_update(
                constraint="uq_playlist_book_source",
                set_={
                    "playlist_name": generated["playlist_name"],
                    "description": generated["description"],
                    "tracks": generated["tracks"],
                    "mood_match_score": generated["mood_match_score"],
                    "genre_match_score": generated["genre_match_score"],
                },
            )
        )
        await self.session.commit()

        return await self.session.scalar(
            select(BookPlaylist).where(
                BookPlaylist.book_id == book_id,
                BookPlaylist.source == generated["source"],
            )
        )

    # -- Matching ---------------------------------------------------------

    async def calculate_music_match(
        self, user_id: uuid.UUID, playlist: BookPlaylist
    ) -> dict[str, Any]:
        """How well a playlist fits this user's stated music taste.

        We only have the signup quiz to go on — Client Credentials auth can't
        read anyone's listening history. To use real history you'd need the
        Authorization Code flow and the `user-top-read` scope.
        """
        if self.session is None:
            raise RuntimeError("MusicService needs a session")

        user = await self.session.get(User, user_id)
        stated = {g.lower() for g in ((user.preferences or {}).get("music_genres") or [])}

        intrinsic = (
            float(playlist.mood_match_score or 50) * 0.5
            + float(playlist.genre_match_score or 50) * 0.5
        )
        if not stated:
            return {
                "score": round(intrinsic, 2),
                "confidence": 0.2,
                "basis": "playlist_quality_only",
            }

        book = await self.session.get(Book, playlist.book_id)
        mapped = {g.lower() for g in music_genres_for_book(book.genres or [], limit=5)}
        overlap = len(stated & mapped) / max(1, len(stated))

        return {
            "score": round(min(100.0, intrinsic * 0.6 + overlap * 100 * 0.4), 2),
            "confidence": round(min(1.0, len(stated) / 3.0), 2),
            "basis": "stated_preferences",
            "matched_genres": sorted(stated & mapped),
        }

    async def get_previews(
        self, track_ids: Sequence[str], source: str = "youtube_music"
    ) -> list[dict[str, Any]]:
        """Resolve 30-second preview URLs.

        With Spotify off, no configured source serves preview clips: YouTube
        Music has no such concept, and a Deezer-curated playlist plays through
        YouTube Music. So `available: false` is now the normal answer, and
        clients should link out via `external_url` instead of expecting audio.

        Spotify previews still resolve if credentials are configured, though
        even then the URL is null for most tracks and most markets.
        """
        if source != "spotify" or not self._init_spotify():
            reason = (
                f"Source '{source}' has no preview clips — play via external_url."
                if source in ("youtube_music", "deezer")
                else f"Previews are not available for source '{source}'."
            )
            return [
                {
                    "track_id": tid,
                    "preview_url": None,
                    "available": False,
                    "reason": reason,
                }
                for tid in track_ids
            ]

        out: list[dict[str, Any]] = []
        # `tracks` accepts up to 50 ids per call — batch to stay in budget.
        for start in range(0, len(track_ids), 50):
            chunk = list(track_ids[start : start + 50])
            await spotify_limiter.acquire()
            try:
                payload = await asyncio.to_thread(self._spotify.tracks, chunk, market="US")
                for track in payload.get("tracks") or []:
                    if not track:
                        continue
                    preview = track.get("preview_url")
                    out.append({
                        "track_id": track["id"],
                        "preview_url": preview,
                        "available": bool(preview),
                        "reason": None if preview else "No preview clip in this market.",
                    })
            except Exception as exc:
                logger.warning("Preview lookup failed: %s", exc)
                out.extend(
                    {
                        "track_id": tid, "preview_url": None, "available": False,
                        "reason": "Preview lookup failed upstream.",
                    }
                    for tid in chunk
                )
        return out
