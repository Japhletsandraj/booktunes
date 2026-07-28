"""SQLAlchemy models mirroring the Supabase schema.

Vector columns use pgvector's 384-dim type to match all-MiniLM-L6-v2.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base

EMBEDDING_DIM = settings.EMBEDDING_DIM


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    reading_level: Mapped[str | None] = mapped_column(Text)
    join_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict, nullable=False
    )

    interactions = relationship(
        "UserBookInteraction", back_populates="user", cascade="all, delete-orphan"
    )
    progress = relationship(
        "ReadingProgress", back_populates="user", cascade="all, delete-orphan"
    )
    preference_embedding = relationship(
        "UserPreferenceEmbedding",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "reading_level IN ('beginner', 'intermediate', 'advanced')",
            name="ck_users_reading_level",
        ),
    )


class Book(Base, TimestampMixin):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    isbn: Mapped[str | None] = mapped_column(Text, index=True)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    moods: Mapped[dict | None] = mapped_column(JSONB)
    reading_level: Mapped[str | None] = mapped_column(Text)
    average_rating: Mapped[float | None] = mapped_column(Numeric(3, 2))
    rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    source_ids: Mapped[dict | None] = mapped_column(JSONB)

    playlists = relationship(
        "BookPlaylist", back_populates="book", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Natural key for dedup when a book arrives without an ISBN.
        Index("idx_books_title_author", "title", "author"),
        Index("idx_books_genres", "genres", postgresql_using="gin"),
    )


class UserBookInteraction(Base, TimestampMixin):
    __tablename__ = "user_book_interactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2))
    review_text: Mapped[str | None] = mapped_column(Text)
    started_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interaction_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_time_spent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User", back_populates="interactions")
    book = relationship("Book")

    __table_args__ = (
        CheckConstraint(
            "status IN ('want_to_read', 'currently_reading', 'read', 'abandoned')",
            name="ck_interactions_status",
        ),
        CheckConstraint("rating >= 0 AND rating <= 5", name="ck_interactions_rating"),
        # One row per user/book — the library is a set, not a log.
        UniqueConstraint("user_id", "book_id", name="uq_interaction_user_book"),
        Index("idx_interactions_user_status", "user_id", "status"),
        Index("idx_interactions_book_user", "book_id", "user_id"),
    )


class ReadingProgress(Base, TimestampMixin):
    __tablename__ = "reading_progress"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    current_page: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0, server_default="0"
    )
    reading_speed: Mapped[float | None] = mapped_column(Numeric(5, 2))
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    total_time_spent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bookmarks: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list, nullable=False
    )
    notes: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list, nullable=False
    )
    device_type: Mapped[str | None] = mapped_column(Text)
    sync_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    user = relationship("User", back_populates="progress")
    book = relationship("Book")

    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_progress_user_book"),
        Index("idx_progress_user", "user_id", "last_read_at"),
    )


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    match_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    confidence_score: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0, server_default="0"
    )
    factor_contributions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("(NOW() + INTERVAL '24 hours')"),
        nullable=False,
    )

    book = relationship("Book")

    __table_args__ = (
        # A user gets one live recommendation per book; regenerating upserts.
        UniqueConstraint("user_id", "book_id", name="uq_recommendation_user_book"),
        Index("idx_recommendations_user", "user_id", "generated_at"),
        Index("idx_recommendations_expires", "expires_at"),
    )


class BookPlaylist(Base, TimestampMixin):
    __tablename__ = "book_playlists"

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    playlist_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_playlist_id: Mapped[str | None] = mapped_column(Text)
    playlist_url: Mapped[str | None] = mapped_column(Text)
    tracks: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list, nullable=False
    )
    mood_match_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    genre_match_score: Mapped[float | None] = mapped_column(Numeric(5, 2))

    book = relationship("Book", back_populates="playlists")

    __table_args__ = (
        CheckConstraint(
            "source IN ('spotify', 'youtube_music', 'custom')",
            name="ck_playlists_source",
        ),
        UniqueConstraint("book_id", "source", name="uq_playlist_book_source"),
    )


class SavedPlaylist(Base):
    """Join table for playlists a user has saved to their own library."""

    __tablename__ = "saved_playlists"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("book_playlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    playlist = relationship("BookPlaylist")

    __table_args__ = (
        UniqueConstraint("user_id", "playlist_id", name="uq_saved_playlist"),
    )


class UserPreferenceEmbedding(Base):
    __tablename__ = "user_preference_embeddings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    short_term_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    long_term_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    preference_weights: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="preference_embedding")


class SystemMetric(Base):
    __tablename__ = "system_metrics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb"), default=dict,
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_metrics_name_time", "metric_name", "timestamp"),)


class RecommendationFeedback(Base):
    """Explicit thumbs up/down on a recommendation — feeds model retraining."""

    __tablename__ = "recommendation_feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    feedback_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "feedback_type IN ('like', 'dislike', 'not_interested', 'already_read')",
            name="ck_feedback_type",
        ),
        Index("idx_feedback_user", "user_id", "created_at"),
    )
