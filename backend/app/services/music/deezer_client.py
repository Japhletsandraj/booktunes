"""Deezer discovery client.

Deezer is the *curation* layer, not a playback source — it returns artist and
track names, never a playable stream (its `preview` field is empty for most
tracks and geo-restricted for the rest). `MusicService` resolves those names
through YouTube Music to get something the client can actually play.

**No API key, no account, no registration.** Deezer's public API is open for
read access; the only requirement is staying under ~50 requests per 5 seconds
per IP, which `deezer_limiter` enforces with room to spare.

Why playlist search rather than a genre chart: Deezer's `/chart/{genre}/tracks`
covers only 22 fixed genres and returns whatever is popular *now*, which is the
same mainstream pop for every book. Searching playlists lets us keep the
free-text mood vocabulary in `genre_mapping` ("dark ambient", "bossa nova",
"trip-hop") and reach the editorial playlists Deezer's own curators maintain.

Every method returns [] on failure rather than raising. Discovery is a
best-effort enrichment; a Deezer outage should degrade the playlist, not fail
the request.
"""

import asyncio
import itertools
from typing import Any

from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set
from app.utils.http import fetch_json
from app.utils.rate_limiter import deezer_limiter

logger = get_logger(__name__)

BASE_URL = "https://api.deezer.com"

# Playlist contents move slowly — a week of caching costs nothing in freshness
# and keeps us far inside the rate limit.
TAG_CACHE_TTL = 60 * 60 * 24 * 7
_CACHE_PREFIX = "music:deezer:v1:"

# How many playlists to consider per tag, and how many to actually drain. We
# over-fetch candidates so the relevance sort has something to choose between.
_PLAYLIST_CANDIDATES = 6
_PLAYLISTS_PER_TAG = 4
_TRACKS_PER_PLAYLIST = 50
# A playlist shorter than this is usually a personal odds-and-ends list rather
# than a genre collection.
_MIN_PLAYLIST_TRACKS = 10
# Cap per artist so one artist-focused playlist can't fill the whole tag.
_MAX_PER_ARTIST = 2

# Deezer needs no credentials, so there is nothing to validate up front. The
# latch instead trips after a run of failures — a network block or an outage
# would otherwise cost every playlist generation a full round of timeouts.
_FAILURE_LIMIT = 5

# Deezer's "Quota limit exceeded". Transient and self-inflicted, so it neither
# counts toward the latch nor gives up on the first sight of it — the quota
# window is only 5 seconds wide.
_QUOTA_ERROR_CODE = 4
_QUOTA_RETRIES = 2
_QUOTA_BACKOFF = 5.0


class DeezerClient:
    """Thin async wrapper over the handful of Deezer endpoints we need."""

    _unavailable = False
    _failures = 0

    @classmethod
    def is_configured(cls) -> bool:
        """True unless repeated failures latched the client off.

        There is no key to check — this reports reachability, not credentials.
        """
        return not cls._unavailable

    @classmethod
    def reset(cls) -> None:
        """Clear the latch — used by tests and to retry after an outage."""
        cls._unavailable = False
        cls._failures = 0

    @classmethod
    def _record_failure(cls) -> None:
        cls._failures += 1
        if cls._failures >= _FAILURE_LIMIT and not cls._unavailable:
            cls._unavailable = True
            logger.warning(
                "Deezer unreachable after %d consecutive failures — discovery "
                "disabled, playlists fall back to YouTube Music keyword search. "
                "Call DeezerClient.reset() to retry.",
                cls._failures,
            )

    @classmethod
    async def _get(cls, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        for attempt in range(_QUOTA_RETRIES + 1):
            if cls._unavailable:
                return None

            await deezer_limiter.acquire()
            try:
                # The shared client pools connections and caps them, which
                # matters here: a nightly batch resolves many tags at once and
                # a client-per-request would open a connection for each.
                payload = await fetch_json(f"{BASE_URL}{path}", params=params)
            except Exception as exc:
                # fetch_json only raises once retries are exhausted, so this is
                # a real outage rather than a blip.
                logger.warning(
                    "Deezer %s failed: %s: %s", path, type(exc).__name__, exc
                )
                cls._record_failure()
                return None

            # None is a 404 or a non-JSON body — a deleted playlist, not an
            # outage, so it must not push the client toward the latch.
            if payload is None:
                return None

            # Deezer reports errors in the body with HTTP 200, so status_code
            # alone is not enough to trust the payload.
            if isinstance(payload, dict) and "error" in payload:
                error = payload["error"] or {}
                if (
                    isinstance(error, dict)
                    and error.get("code") == _QUOTA_ERROR_CODE
                    and attempt < _QUOTA_RETRIES
                ):
                    logger.info(
                        "Deezer quota hit on %s — backing off %.0fs", path, _QUOTA_BACKOFF
                    )
                    await asyncio.sleep(_QUOTA_BACKOFF)
                    continue
                logger.warning("Deezer %s error: %s", path, error)
                # A quota error means Deezer is up and we asked too fast, so it
                # must not push the client toward the unavailable latch.
                if not (isinstance(error, dict) and error.get("code") == _QUOTA_ERROR_CODE):
                    cls._record_failure()
                return None

            cls._failures = 0
            return payload

        return None

    @classmethod
    async def tag_top_tracks(cls, tag: str, limit: int = 30) -> list[dict[str, str]]:
        """Tracks for a mood/genre tag, as {"title", "artist"} pairs.

        Found by searching Deezer for playlists matching the tag and draining
        the most relevant ones. Results are interleaved across playlists rather
        than concatenated — one playlist's ordering is one curator's taste, and
        taking the first N from it gives a single-flavour result.
        """
        cache_key = f"{_CACHE_PREFIX}tag:{tag}:{limit}"
        if (cached := await cache_get(cache_key)) is not None:
            return cached

        playlists = await cls._search_playlists(tag)
        if not playlists:
            return []

        pools = await asyncio.gather(
            *(cls._playlist_tracks(p["id"]) for p in playlists)
        )
        tracks = cls._interleave(pools, limit)

        await cache_set(cache_key, tracks, ttl=TAG_CACHE_TTL)
        return tracks

    @classmethod
    async def _search_playlists(cls, tag: str) -> list[dict[str, Any]]:
        """Find the playlists worth draining for a tag, best match first."""
        payload = await cls._get(
            "/search/playlist", {"q": tag, "limit": _PLAYLIST_CANDIDATES}
        )
        if not payload:
            return []

        candidates = [
            item
            for item in (payload.get("data") or [])
            if isinstance(item, dict)
            and item.get("id")
            and (item.get("nb_tracks") or 0) >= _MIN_PLAYLIST_TRACKS
        ]
        candidates.sort(key=lambda p: cls._relevance(p, tag), reverse=True)
        return candidates[:_PLAYLISTS_PER_TAG]

    @staticmethod
    def _relevance(playlist: dict[str, Any], tag: str) -> int:
        """Score a search hit against the tag it was searched for.

        Deezer's playlist search is fuzzy — "melancholy" returns chart-pop
        playlists alongside genuinely melancholic ones. Preferring a literal
        title match, and Deezer's own editors, keeps the result on-tag.
        """
        title = (playlist.get("title") or "").lower()
        owner = ((playlist.get("user") or {}).get("name") or "").lower()

        words = [w for w in tag.lower().replace("-", " ").split() if len(w) > 2]
        score = sum(2 for word in words if word in title)
        # Editorial playlists ("… - Deezer Jazz & Blues Editor") are curated
        # per genre; user playlists match the query far more loosely.
        if "deezer" in owner:
            score += 2
        return score

    @classmethod
    async def _playlist_tracks(cls, playlist_id: Any) -> list[dict[str, str]]:
        payload = await cls._get(
            f"/playlist/{playlist_id}/tracks", {"limit": _TRACKS_PER_PLAYLIST}
        )
        if not payload:
            return []

        return [
            parsed
            for item in (payload.get("data") or [])
            if (parsed := cls._parse_name_pair(item)) is not None
        ]

    @staticmethod
    def _interleave(
        pools: list[list[dict[str, str]]], cap: int
    ) -> list[dict[str, str]]:
        """Round-robin the per-playlist lists, deduped and capped per artist."""
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        per_artist: dict[str, int] = {}

        for row in itertools.zip_longest(*pools):
            for pair in row:
                if not pair:
                    continue
                artist = pair["artist"].lower()
                key = (artist, pair["title"].lower())
                if key in seen or per_artist.get(artist, 0) >= _MAX_PER_ARTIST:
                    continue
                seen.add(key)
                per_artist[artist] = per_artist.get(artist, 0) + 1
                out.append(pair)
                if len(out) >= cap:
                    return out
        return out

    @staticmethod
    def _parse_name_pair(item: dict[str, Any]) -> dict[str, str] | None:
        """Pull {"title", "artist"} out of a Deezer track object.

        `artist` is a nested object on the playlist endpoint but a bare string
        on some search shapes, so both are handled.
        """
        if not isinstance(item, dict):
            return None
        title = (item.get("title") or "").strip()
        artist_field = item.get("artist")
        if isinstance(artist_field, dict):
            artist = (artist_field.get("name") or "").strip()
        else:
            artist = (artist_field or "").strip()

        if not title or not artist:
            return None
        return {"title": title, "artist": artist}
