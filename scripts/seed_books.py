"""Seed the book catalogue from Open Library.

    python -m scripts.seed_books                # full seed (~1000 books)
    python -m scripts.seed_books --genres fantasy,mystery --limit 50

Takes 20-40 minutes for a full run — most of it is rate-limited waiting on
Open Library, which is intentional. Safe to interrupt and re-run: books are
deduplicated on ISBN, Open Library work id, then title+author.
"""

import argparse
import asyncio

from app.core.database import session_scope
from app.core.logging_config import get_logger, setup_logging
from app.core.redis_client import close_redis, init_redis
from app.services.book_ingestion import SEED_GENRES, BookIngestionService

setup_logging()
logger = get_logger(__name__)


async def main(genres: dict) -> None:
    await init_redis()
    try:
        async with session_scope() as session:
            result = await BookIngestionService(session).initial_seed(genres)
        logger.info("Totals: %s", result["totals"])
        logger.info(
            "Now rebuild the vector index: python -m scripts.rebuild_vector_index"
        )
    finally:
        await close_redis()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Booktunes catalogue")
    parser.add_argument(
        "--genres", help="Comma-separated genres (default: the full seed plan)"
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Books per genre when --genres is given"
    )
    args = parser.parse_args()

    plan = (
        {g.strip(): args.limit for g in args.genres.split(",") if g.strip()}
        if args.genres
        else SEED_GENRES
    )
    asyncio.run(main(plan))
