"""Stock the catalogue on demand, so a fresh deployment is not an empty shelf.

The full seed (``scripts.seed_books``) pulls ~1000 books and takes 20-40
minutes. That is the right shape for a one-off admin job and the wrong shape
for a user who just opened the home page: until someone remembers to run it,
every shelf renders empty and the product looks broken.

This module fills the gap by ingesting a small batch for whatever the caller
actually asked for — the trending row, or one genre shelf — the first time it
is found empty. Two properties make that safe to hang off a read endpoint:

* It never runs inline. Callers schedule it through ``BackgroundTasks`` so the
  response goes out immediately with whatever is already stored. A cold
  Open Library round-trip plus enrichment runs into tens of seconds, which is
  far past what a page load should wait for, and past Render's proxy timeout.

* It is claimed before it runs. Without that, the empty-shelf state is exactly
  the state that fans out — a first visit fires trending plus four genre
  shelves at once, and each one would kick off its own ingest of the same
  books.

Steady state is unaffected: once a scope has rows, nothing here is scheduled,
so this costs one Redis GET per request against a stocked catalogue.
"""

from __future__ import annotations

from app.core.logging_config import get_logger
from app.core.redis_client import get_client

logger = get_logger(__name__)

# Small on purpose. This is the "show the user something now" path, not the
# real seed — 12 books fills a scroll row, and the nightly fetch_new_books
# cron deepens the catalogue from there.
AUTOFILL_LIMIT = 12

# How long a claim is held. Long enough that a slow ingest is not lapped by the
# next request, short enough that a crashed worker cannot wedge a scope shut.
CLAIM_TTL_SECONDS = 900

# Once a scope has succeeded, suppress re-entry for a while even if it comes
# back under threshold — a genre Open Library simply has little of should not
# be retried on every page load.
COOLDOWN_SECONDS = 21_600  # 6h, matching the ingest crons

# Fallback when Redis is down. Single worker on the free plan (Dockerfile pins
# --workers 1), so a process-local set is a real guard there rather than a
# token effort; with several workers it degrades to one redundant ingest each,
# which dedup on the ingestion side absorbs.
_inflight: set[str] = set()


def _key(scope: str) -> str:
    return f"catalogue:autofill:{scope}"


async def _claim(scope: str) -> bool:
    """Take the right to stock `scope`, or report that someone else has it."""
    client = get_client()
    if client is None:
        if scope in _inflight:
            return False
        _inflight.add(scope)
        return True

    try:
        # NX makes the check and the claim one operation. Testing existence and
        # then setting would leave a window where concurrent shelf requests all
        # observe "free" and all proceed.
        claimed = await client.set(_key(scope), "1", ex=CLAIM_TTL_SECONDS, nx=True)
    except Exception as exc:
        # A cache that is down must not take book ingestion down with it — fall
        # back to the in-process guard.
        logger.warning("autofill claim failed for %s, proceeding: %s", scope, exc)
        if scope in _inflight:
            return False
        _inflight.add(scope)
        return True

    return bool(claimed)


async def _finish(scope: str, *, cooldown: bool) -> None:
    client = get_client()
    _inflight.discard(scope)
    if client is None:
        return
    try:
        if cooldown:
            await client.set(_key(scope), "done", ex=COOLDOWN_SECONDS)
        else:
            # Failed: drop the claim so the next request may retry rather than
            # waiting out the full TTL with an empty shelf.
            await client.delete(_key(scope))
    except Exception as exc:
        logger.warning("autofill claim release failed for %s: %s", scope, exc)


async def stock_genre(genre: str, limit: int = AUTOFILL_LIMIT) -> None:
    """Ingest a small batch for one genre. Safe to call when already stocked."""
    scope = f"genre:{genre}"
    if not await _claim(scope):
        logger.debug("autofill for %s already claimed, skipping", scope)
        return

    # Its own session: this runs after the response, by which point the
    # request-scoped session from get_db is closed.
    from app.core.database import session_scope
    from app.services.book_ingestion import BookIngestionService

    ok = False
    try:
        async with session_scope() as session:
            result = await BookIngestionService(session).ingest_genre(genre, limit)
        logger.info("autofill %s: %s", scope, result)
        ok = True
    except Exception:
        logger.exception("autofill failed for %s", scope)
    finally:
        await _finish(scope, cooldown=ok)
