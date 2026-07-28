"""Rebuild the ivfflat index on books.embedding.

Run after seeding or any large ingest:

    python -m scripts.rebuild_vector_index

An ivfflat index clusters the vectors present when it is built. Built on an
empty (or much smaller) table, its centroids no longer represent the data and
recall degrades silently — queries still return results, just worse ones.
"""

import asyncio

from sqlalchemy import func, select, text

from app.core.database import session_scope
from app.core.logging_config import get_logger, setup_logging
from app.models import Book

setup_logging()
logger = get_logger(__name__)


async def main() -> None:
    async with session_scope() as session:
        rows = int(
            await session.scalar(
                select(func.count()).select_from(Book).where(Book.embedding.is_not(None))
            ) or 0
        )
        if rows == 0:
            logger.warning("No embedded books — seed the catalogue first.")
            return

        # pgvector's guidance for <1M rows: lists ≈ rows / 1000.
        lists = max(10, min(1000, rows // 1000))
        logger.info("Rebuilding index over %d vectors with lists=%d", rows, lists)

        await session.execute(text("DROP INDEX IF EXISTS idx_books_embedding"))
        await session.execute(
            text(
                f"CREATE INDEX idx_books_embedding ON books "
                f"USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})"
            )
        )
        await session.execute(text("ANALYZE books"))
        logger.info("Index rebuilt.")


if __name__ == "__main__":
    asyncio.run(main())
