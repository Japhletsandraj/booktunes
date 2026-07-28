"""Application settings, loaded from environment / .env."""

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "insecure-dev-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- Database ---
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/booktunes"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE: int = 1800

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- External APIs ---
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    GOOGLE_BOOKS_API_KEY: str = ""
    CLOUDINARY_URL: str = ""
    RESEND_API_KEY: str = ""
    ALERT_EMAIL_TO: str = ""

    # --- Monitoring ---
    SENTRY_DSN: str = ""
    ALERT_WEBHOOK_URL: str = ""

    # --- CORS / hosts ---
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    ALLOWED_HOSTS: list[str] = Field(default_factory=lambda: ["*"])

    # --- ML ---
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM: int = 384
    EMBEDDING_CACHE_DIR: str = ".model_cache"
    MODEL_STORAGE_DIR: str = ".models"
    RECOMMENDATION_CACHE_TTL: int = 3600

    # --- Free-tier quota ceilings (see services/cost_monitor.py) ---
    MAX_DB_SIZE_MB: int = 500
    MAX_REDIS_COMMANDS_PER_DAY: int = 10_000
    MAX_CLOUDINARY_STORAGE_GB: int = 25
    QUOTA_ALERT_THRESHOLD: float = 0.8  # alert at 80% of any ceiling

    @field_validator("CORS_ORIGINS", "ALLOWED_HOSTS", mode="before")
    @classmethod
    def _split_csv(cls, v):
        """Accept either a JSON list or a plain comma-separated string."""
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return []
            if text.startswith("[") and text.endswith("]"):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return v

    @classmethod
    def parse_env_var(cls, field_name: str, raw_val: str) -> Any:
        """Allow plain comma-separated values for list settings from .env files."""
        if field_name in {"CORS_ORIGINS", "ALLOWED_HOSTS"}:
            return cls._split_csv(raw_val)
        return super().parse_env_var(field_name, raw_val)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def async_database_url(self) -> str:
        """DATABASE_URL normalised to the asyncpg driver."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):  # Heroku/Render legacy scheme
            url = url.replace("postgres://", "postgresql://", 1)
        if "+" not in url.split("://", 1)[0]:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """DATABASE_URL normalised to psycopg2 — used by Alembic."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
