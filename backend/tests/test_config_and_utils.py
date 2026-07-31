"""Settings normalisation and the outbound rate limiter."""

import asyncio
import time

import pytest

from app.core.config import Settings
from app.utils.rate_limiter import RateLimiter


class TestDatabaseUrlNormalisation:
    def test_plain_url_gets_asyncpg_driver(self):
        s = Settings(DATABASE_URL="postgresql://u:p@host:5432/db")
        assert s.async_database_url.startswith("postgresql+asyncpg://")

    def test_legacy_postgres_scheme_upgraded(self):
        """Render and Heroku still hand out `postgres://`, which SQLAlchemy 2
        refuses outright."""
        s = Settings(DATABASE_URL="postgres://u:p@host:5432/db")
        assert s.async_database_url.startswith("postgresql+asyncpg://")
        assert s.sync_database_url.startswith("postgresql://")

    def test_existing_driver_preserved(self):
        s = Settings(DATABASE_URL="postgresql+asyncpg://u:p@host/db")
        assert s.async_database_url == "postgresql+asyncpg://u:p@host/db"

    def test_sync_url_strips_asyncpg(self):
        """Alembic runs sync — leaving +asyncpg in place breaks migrations."""
        s = Settings(DATABASE_URL="postgresql+asyncpg://u:p@host/db")
        assert "asyncpg" not in s.sync_database_url


class TestCsvSettings:
    def test_comma_separated_origins_parsed(self):
        s = Settings(CORS_ORIGINS="http://a.com, http://b.com")
        assert s.CORS_ORIGINS == ["http://a.com", "http://b.com"]

    def test_blank_entries_dropped(self):
        s = Settings(CORS_ORIGINS="http://a.com,,  ,http://b.com")
        assert s.CORS_ORIGINS == ["http://a.com", "http://b.com"]

    def test_list_passed_through(self):
        s = Settings(ALLOWED_HOSTS=["example.com"])
        assert s.ALLOWED_HOSTS == ["example.com"]

    def test_comma_separated_origins_from_env(self, monkeypatch):
        """The deploy-breaking case: pydantic-settings JSON-decodes complex
        fields inside the env source, before validators run. Only the init-kwarg
        path was covered before, so this shipped and crashed on Render."""
        monkeypatch.setenv("CORS_ORIGINS", "https://booktunes.vercel.app,http://localhost:3000")
        s = Settings()
        assert s.CORS_ORIGINS == ["https://booktunes.vercel.app", "http://localhost:3000"]

    def test_comma_separated_hosts_from_env(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_HOSTS", "booktunes-api.onrender.com,localhost")
        assert Settings().ALLOWED_HOSTS == ["booktunes-api.onrender.com", "localhost"]

    def test_json_list_from_env_still_works(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", '["https://a.com", "https://b.com"]')
        assert Settings().CORS_ORIGINS == ["https://a.com", "https://b.com"]

    def test_non_csv_field_still_decoded_normally(self, monkeypatch):
        """The source override must be scoped to the CSV fields only."""
        monkeypatch.setenv("DB_POOL_SIZE", "12")
        assert Settings().DB_POOL_SIZE == 12


class TestEnvironmentFlag:
    def test_production_detected(self):
        assert Settings(ENVIRONMENT="production").is_production

    def test_other_environments_are_not_production(self):
        assert not Settings(ENVIRONMENT="development").is_production
        assert not Settings(ENVIRONMENT="staging").is_production


class TestRateLimiter:
    async def test_allows_burst_up_to_capacity(self):
        limiter = RateLimiter(rate=5, period=60.0)
        started = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        # The initial bucket is full, so five calls must not block.
        assert time.monotonic() - started < 0.2

    async def test_blocks_once_tokens_exhausted(self):
        # 10/second: the 3rd call must wait for a refill.
        limiter = RateLimiter(rate=2, period=0.2)
        await limiter.acquire()
        await limiter.acquire()

        started = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - started > 0.02

    async def test_refills_over_time(self):
        limiter = RateLimiter(rate=2, period=0.1)
        await limiter.acquire()
        await limiter.acquire()
        await asyncio.sleep(0.15)

        started = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - started < 0.05

    async def test_context_manager(self):
        limiter = RateLimiter(rate=2, period=1.0)
        async with limiter:
            pass

    def test_rejects_invalid_rate(self):
        with pytest.raises(ValueError):
            RateLimiter(rate=0)
