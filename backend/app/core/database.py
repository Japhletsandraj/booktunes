"""Async SQLAlchemy engine + session management.

Celery workers cannot share the API's event loop, so :func:`session_scope` is
provided as a standalone async context manager for use inside ``asyncio.run``.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict:
    kwargs: dict = {
        "echo": False,
        "pool_pre_ping": True,  # Supabase drops idle connections
        "future": True,
    }
    if "sqlite" in settings.async_database_url:
        # Test/dev fallback — SQLite has no real pooling.
        kwargs["poolclass"] = NullPool
    else:
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
            # Supabase's pooler doesn't support prepared-statement caching
            # across connections; disabling avoids "prepared statement already
            # exists" errors when the pooler multiplexes.
            connect_args={"statement_cache_size": 0, "server_settings": {
                "application_name": "booktunes-api"
            }},
        )
    return kwargs


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Create the engine on first use.

    Built lazily rather than at import time so that importing a model doesn't
    require the DB driver to be installed or DATABASE_URL to be valid — which
    would make unit tests, Alembic and `--help` all depend on a live database.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.async_database_url, **_engine_kwargs())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session that rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Standalone session for Celery tasks and scripts."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Health probe — also verifies the pgvector extension is present."""
    from sqlalchemy import text

    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
            result = await session.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
            if result.scalar() is None:
                logger.warning(
                    "pgvector extension not installed — semantic search will fail. "
                    "Run: CREATE EXTENSION IF NOT EXISTS vector;"
                )
        return True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return False


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
