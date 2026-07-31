"""Async token-bucket rate limiter for outbound API calls.

Process-local. On the free tier we run a single web dyno and a single worker,
so a shared (Redis-backed) limiter would cost commands we can't spare. If you
scale past one worker per external API, move this state into Redis.
"""

import asyncio
import time


class RateLimiter:
    """Allows ``rate`` operations per ``period`` seconds, smoothed."""

    def __init__(self, rate: int, period: float = 60.0, name: str = "default"):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.period = period
        self.name = name
        self._tokens = float(rate)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Block until ``tokens`` are available."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(
                    self.rate, self._tokens + elapsed * (self.rate / self.period)
                )
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit * (self.period / self.rate)
            # Sleep outside the lock so other callers can still refill.
            await asyncio.sleep(min(wait, self.period))

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        return None


# Per-provider limiters, sized to each provider's documented ceiling with
# headroom. Open Library asks for <=100/min and will 403 persistent abusers.
open_library_limiter = RateLimiter(rate=60, period=60.0, name="open_library")
google_books_limiter = RateLimiter(rate=50, period=60.0, name="google_books")
# Spotify's limit is a rolling ~180/min; stay well under to avoid 429 backoffs.
spotify_limiter = RateLimiter(rate=120, period=60.0, name="spotify")
# Deezer allows ~50 requests per 5s per IP (there is no key, so the quota is
# shared by everything on the host). Expressed in Deezer's own units on
# purpose: the bucket starts full, so `rate` is also the burst size, and a
# per-minute figure would let a nightly batch fire hundreds of requests at once
# and earn "Quota limit exceeded" on the first second.
deezer_limiter = RateLimiter(rate=40, period=5.0, name="deezer")
# ytmusicapi scrapes an unofficial endpoint, so this stays conservative — but
# it is now the critical path (every curated track needs one search to become
# playable), so 30/min made a 20-track playlist take ~40s. 60/min with the
# per-track resolution cache is the compromise.
youtube_limiter = RateLimiter(rate=60, period=60.0, name="youtube_music")
