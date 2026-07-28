"""Scheduled Celery tasks.

Every service in this project is async, but Celery workers are synchronous.
Rather than maintain a second, sync copy of each service, each task body is an
async function run through :func:`run_async`, which owns a fresh event loop per
task. That keeps one implementation of the business logic.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.celery_app import celery_app
from app.core.database import session_scope
from app.core.logging_config import get_logger, setup_logging
from app.core.redis_client import close_redis, init_redis
from app.models import Book, BookPlaylist, SystemMetric, User

setup_logging()
logger = get_logger(__name__)


def run_async(coro_factory: Callable[[], Coroutine]) -> Any:
    """Run an async task body in a dedicated event loop.

    A fresh loop per task avoids the "attached to a different loop" errors you
    get when asyncpg connections outlive the loop that created them — which is
    exactly what happens if you reuse a module-level loop across Celery tasks.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_with_redis(coro_factory))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


async def _with_redis(coro_factory: Callable[[], Coroutine]) -> Any:
    await init_redis()
    try:
        return await coro_factory()
    finally:
        await close_redis()


async def _record_metric(session, name: str, value: float, meta: dict[str, Any]) -> None:
    session.add(SystemMetric(metric_name=name, value=value, meta=meta))


# --- Model training ------------------------------------------------------

@celery_app.task(name="app.tasks.retrain_models", bind=True, max_retries=2)
def retrain_models(self) -> dict[str, Any]:
    """Retrain the ALS collaborative-filtering model (daily, 03:00 UTC)."""

    async def _run():
        from app.services.ai.recommendation_engine import BookRecommendationEngine

        async with session_scope() as session:
            engine = BookRecommendationEngine(session)
            metrics = await engine.train_and_swap()
            await _record_metric(
                session, "model.als_training", metrics.get("interactions", 0), metrics
            )
            return metrics

    try:
        result = run_async(_run)
        logger.info("retrain_models complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("retrain_models failed")
        raise self.retry(exc=exc, countdown=300) from exc


# --- Recommendations -----------------------------------------------------

@celery_app.task(name="app.tasks.update_recommendations", bind=True, max_retries=2)
def update_recommendations(self, batch_size: int = 50) -> dict[str, Any]:
    """Precompute recommendations for recently active users (daily, 04:00)."""

    async def _run():
        from app.services.ai.recommendation_engine import BookRecommendationEngine

        cutoff = datetime.now(UTC) - timedelta(days=30)
        processed = failed = 0

        async with session_scope() as session:
            user_ids = (
                await session.scalars(
                    select(User.id)
                    .where(User.last_active >= cutoff)
                    .order_by(User.last_active.desc())
                    # Cap the run so it can't exceed the 25-minute soft limit
                    # on a free worker. The rest are picked up tomorrow.
                    .limit(500)
                )
            ).all()

            engine = BookRecommendationEngine(session)
            for user_id in user_ids:
                try:
                    await engine.get_personalized_recommendations(
                        user_id, limit=20, use_cache=False
                    )
                    processed += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Recommendations failed for %s: %s", user_id, exc)

            await _record_metric(
                session, "batch.recommendations", processed,
                {"failed": failed, "total": len(user_ids)},
            )

        return {"processed": processed, "failed": failed}

    try:
        result = run_async(_run)
        logger.info("update_recommendations complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("update_recommendations failed")
        raise self.retry(exc=exc, countdown=300) from exc


# --- Preferences ---------------------------------------------------------

@celery_app.task(name="app.tasks.update_user_preferences", bind=True, max_retries=2)
def update_user_preferences(self) -> dict[str, Any]:
    """Rebuild preference vectors for users with new activity (every 6h)."""

    async def _run():
        from app.services.ai.preference_learner import UserPreferenceLearner

        updated = skipped = failed = 0
        async with session_scope() as session:
            learner = UserPreferenceLearner(session)
            # Delta only: users whose interactions post-date their vector.
            user_ids = await learner.users_needing_update(hours=6, limit=500)

            for user_id in user_ids:
                try:
                    result = await learner.update_user_preferences(user_id)
                    if result.get("updated"):
                        updated += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Preference update failed for %s: %s", user_id, exc)

            await _record_metric(
                session, "batch.preferences", updated,
                {"skipped": skipped, "failed": failed, "candidates": len(user_ids)},
            )

        return {"updated": updated, "skipped": skipped, "failed": failed}

    try:
        result = run_async(_run)
        logger.info("update_user_preferences complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("update_user_preferences failed")
        raise self.retry(exc=exc, countdown=300) from exc


# --- Ingestion -----------------------------------------------------------

@celery_app.task(name="app.tasks.fetch_new_books", bind=True, max_retries=2)
def fetch_new_books(self, per_genre: int = 20) -> dict[str, Any]:
    """Incrementally top up the catalogue (daily, 02:00).

    Small per-genre limits keep the run short and the database inside the
    500MB free tier; dedup makes repeated runs cheap.
    """

    async def _run():
        from app.services.book_ingestion import SEED_GENRES, BookIngestionService

        async with session_scope() as session:
            service = BookIngestionService(session)
            totals = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}

            for genre in list(SEED_GENRES)[:8]:  # rotate through genres daily
                try:
                    result = await service.ingest_genre(genre, per_genre)
                    for key, value in result.items():
                        totals[key] += value
                except Exception as exc:
                    logger.warning("Ingest for genre %s failed: %s", genre, exc)

            backfilled = await service.backfill_embeddings(limit=200)
            totals["embeddings_backfilled"] = backfilled

            await _record_metric(session, "batch.ingestion", totals["inserted"], totals)

        return totals

    try:
        result = run_async(_run)
        logger.info("fetch_new_books complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("fetch_new_books failed")
        raise self.retry(exc=exc, countdown=600) from exc


@celery_app.task(name="app.tasks.seed_catalogue")
def seed_catalogue() -> dict[str, Any]:
    """One-off full seed (~1000 books). Trigger manually:

        celery -A app.core.celery_app call app.tasks.seed_catalogue

    Takes 20-40 minutes depending on upstream latency, so it is intentionally
    NOT on the beat schedule.
    """

    async def _run():
        from app.services.book_ingestion import BookIngestionService

        async with session_scope() as session:
            return await BookIngestionService(session).initial_seed()

    result = run_async(_run)
    logger.info("seed_catalogue complete: %s", result.get("totals"))
    return result


# --- Playlists -----------------------------------------------------------

@celery_app.task(name="app.tasks.generate_playlists", bind=True, max_retries=1)
def generate_playlists(self, limit: int = 40) -> dict[str, Any]:
    """Build playlists for books that lack one (daily, 05:00).

    Capped per run to stay well inside Spotify's rate limits — every playlist
    costs several search calls.
    """

    async def _run():
        from app.services.music.music_service import MusicService

        created = failed = 0
        async with session_scope() as session:
            book_ids = (
                await session.scalars(
                    select(Book.id)
                    .outerjoin(BookPlaylist, BookPlaylist.book_id == Book.id)
                    .where(BookPlaylist.id.is_(None))
                    # Prioritise books people actually see.
                    .order_by(Book.rating_count.desc().nullslast())
                    .limit(limit)
                )
            ).all()

            service = MusicService(session)
            for book_id in book_ids:
                try:
                    playlist = await service.get_or_create_playlist(book_id)
                    if playlist is not None:
                        created += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Playlist generation failed for %s: %s", book_id, exc)

            await _record_metric(
                session, "batch.playlists", created,
                {"failed": failed, "candidates": len(book_ids)},
            )

        return {"created": created, "failed": failed}

    try:
        result = run_async(_run)
        logger.info("generate_playlists complete: %s", result)
        return result
    except Exception as exc:
        logger.exception("generate_playlists failed")
        raise self.retry(exc=exc, countdown=600) from exc


# --- Housekeeping --------------------------------------------------------

@celery_app.task(name="app.tasks.cleanup_cache")
def cleanup_cache() -> dict[str, Any]:
    """Prune expired rows and stale cache keys (daily, 23:00)."""

    async def _run():
        from app.core.redis_client import cache_delete_pattern
        from app.services.cost_monitor import CostMonitor

        async with session_scope() as session:
            removed = await CostMonitor(session).cleanup()

        # Redis TTLs expire keys on their own; this only sweeps the patterns
        # we deliberately write without one.
        removed["stale_search_cache"] = await cache_delete_pattern("music:search:v1:*")
        return removed

    result = run_async(_run)
    logger.info("cleanup_cache complete: %s", result)
    return result


@celery_app.task(name="app.tasks.aggregate_metrics")
def aggregate_metrics() -> dict[str, Any]:
    """Sample quotas and alert on breaches (hourly)."""

    async def _run():
        from app.services.cost_monitor import CostMonitor

        async with session_scope() as session:
            monitor = CostMonitor(session)
            report = await monitor.collect()
            await monitor.record(report)
            alerted = await monitor.alert_if_needed(report)
            return {
                "quotas": report["quotas"],
                "breaches": report["breaches"],
                "alerted": alerted,
            }

    result = run_async(_run)
    if result["breaches"]:
        logger.warning("Free-tier quota breaches: %s", result["breaches"])
    return result


@celery_app.task(name="app.tasks.weekly_usage_report")
def weekly_usage_report() -> dict[str, Any]:
    """Email a usage digest (Mondays, 09:00 UTC)."""

    async def _run():
        from app.services.cost_monitor import CostMonitor, send_usage_email

        async with session_scope() as session:
            report = await CostMonitor(session).collect()
        sent = await send_usage_email(report)
        return {"sent": sent, "quotas": report["quotas"]}

    result = run_async(_run)
    logger.info("weekly_usage_report complete (sent=%s)", result["sent"])
    return result


@celery_app.task(name="app.tasks.rebuild_vector_index")
def rebuild_vector_index() -> dict[str, Any]:
    """Rebuild the ivfflat index with a list count sized to the current row
    count. Run after any large ingest — see migrations/0002 for why."""

    async def _run():
        from sqlalchemy import text

        async with session_scope() as session:
            rows = int(
                await session.scalar(
                    select(func.count()).select_from(Book).where(Book.embedding.is_not(None))
                ) or 0
            )
            lists = max(10, min(1000, rows // 1000)) if rows else 10

            await session.execute(text("DROP INDEX IF EXISTS idx_books_embedding"))
            await session.execute(
                text(
                    f"CREATE INDEX idx_books_embedding ON books "
                    f"USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
                )
            )
            await session.execute(text("ANALYZE books"))
            return {"rows": rows, "lists": lists}

    result = run_async(_run)
    logger.info("rebuild_vector_index complete: %s", result)
    return result
