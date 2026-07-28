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
# ytmusicapi scrapes an unofficial endpoint — be conservative.
youtube_limiter = RateLimiter(rate=30, period=60.0, name="youtube_music")
