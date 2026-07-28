"""Redis cache wrapper.

Upstash's free tier allows 10,000 commands/day, so every call here increments a
daily counter (itself one command) that ``cost_monitor`` reads. All operations
degrade to a no-op miss when Redis is unreachable — the cache must never be the
reason a request fails.
"""

import json
from datetime import date
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None

COMMAND_COUNTER_PREFIX = "meta:cmdcount:"


async def init_redis() -> aioredis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        _client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        await _client.ping()
        logger.info("Redis connected")
    except Exception as exc:
        logger.error("Redis unavailable, running without cache: %s", exc)
        _client = None
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> aioredis.Redis | None:
    return _client


async def _track() -> None:
    """Increment today's command counter (best effort)."""
    if _client is None:
        return
    try:
        key = f"{COMMAND_COUNTER_PREFIX}{date.today().isoformat()}"
        pipe = _client.pipeline()
        pipe.incr(key)
        pipe.expire(key, 172_800)  # keep 2 days for the daily report
        await pipe.execute()
    except Exception:
        pass


async def cache_get(key: str) -> Any | None:
    if _client is None:
        return None
    try:
        raw = await _client.get(key)
        await _track()
        return json.loads(raw) if raw is not None else None
    except Exception as exc:
        logger.debug("cache_get(%s) failed: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: int = 3600) -> bool:
    if _client is None:
        return False
    try:
        await _client.set(key, json.dumps(value, default=str), ex=ttl)
        await _track()
        return True
    except Exception as exc:
        logger.debug("cache_set(%s) failed: %s", key, exc)
        return False


async def cache_delete(*keys: str) -> int:
    if _client is None or not keys:
        return 0
    try:
        deleted = await _client.delete(*keys)
        await _track()
        return deleted
    except Exception as exc:
        logger.debug("cache_delete failed: %s", exc)
        return 0


async def cache_delete_pattern(pattern: str) -> int:
    """Delete by glob. Uses SCAN (not KEYS) so it never blocks the server."""
    if _client is None:
        return 0
    deleted = 0
    try:
        async for key in _client.scan_iter(match=pattern, count=200):
            await _client.delete(key)
            deleted += 1
        await _track()
    except Exception as exc:
        logger.debug("cache_delete_pattern(%s) failed: %s", pattern, exc)
    return deleted


async def get_daily_command_count() -> int:
    if _client is None:
        return 0
    try:
        raw = await _client.get(f"{COMMAND_COUNTER_PREFIX}{date.today().isoformat()}")
        return int(raw or 0)
    except Exception:
        return 0


async def check_redis() -> bool:
    if _client is None:
        return False
    try:
        await _client.ping()
        return True
    except Exception:
        return False


# --- Rate limiting -------------------------------------------------------

async def rate_limit_hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns ``(allowed, current_count)``.

    Fails open: if Redis is down, requests are allowed through rather than
    locking every user out of the API.
    """
    if _client is None:
        return True, 0
    try:
        full_key = f"ratelimit:{key}"
        pipe = _client.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, window_seconds)
        count, _ = await pipe.execute()
        return int(count) <= limit, int(count)
    except Exception:
        return True, 0
