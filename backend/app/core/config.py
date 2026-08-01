"""Application settings, loaded from environment / .env."""

import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Settings that accept either a JSON list or a plain comma-separated string.
CSV_LIST_FIELDS = frozenset({"CORS_ORIGINS", "ALLOWED_HOSTS"})


class _CsvListSourceMixin:
    """Stop pydantic-settings from JSON-decoding the CSV list settings.

    Any field typed as a complex type (list/dict) gets `json.loads()` applied to
    its raw env value *inside the settings source* — before field validators
    ever run. So `CORS_ORIGINS=https://a.com,https://b.com` raises
    JSONDecodeError and never reaches `Settings._split_csv`. Handing the raw
    string through unchanged lets the validator do the parsing.
    """

    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name in CSV_LIST_FIELDS:
            return value
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class _CsvEnvSource(_CsvListSourceMixin, EnvSettingsSource):
    pass


class _CsvDotEnvSource(_CsvListSourceMixin, DotEnvSettingsSource):
    pass


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
    # Discovery (mood/genre tags -> tracks) runs on Deezer's public API and
    # YouTube Music, neither of which takes a key — so there is nothing to
    # configure here for playlists to work.
    #
    # Spotify is optional and off unless credentials are set — apps created
    # after 2024-11-27 lose /recommendations, and search now needs the app
    # owner to hold Premium.
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
    # No trailing slashes — browsers send Origin as scheme://host[:port] only,
    # so "https://example.com/" never matches and silently blocks the origin.
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "https://booktunes-murex.vercel.app",
        ]
    )
    # Vercel mints a fresh origin for every deployment
    # (booktunes-<hash>-<team>.vercel.app), so the preview URLs can never be
    # enumerated in CORS_ORIGINS. Match them by pattern instead. Anchored at
    # both ends: Starlette uses re.fullmatch, but an unanchored pattern that
    # someone later switches to re.search would allow evil-vercel.app.attacker.com.
    CORS_ORIGIN_REGEX: str = ""
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Swap in the env sources that leave CSV list values alone.

        Order is pydantic-settings' default precedence: init > env > .env > secrets.
        """
        return (
            init_settings,
            _CsvEnvSource(settings_cls),
            _CsvDotEnvSource(settings_cls),
            file_secret_settings,
        )

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
