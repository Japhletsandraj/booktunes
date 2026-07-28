"""Initial Booktunes schema with pgvector

Revision ID: 0001
Revises:
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text()),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("reading_level", sa.Text()),
        sa.Column("join_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_active", sa.DateTime(timezone=True)),
        sa.Column("preferences", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint(
            "reading_level IN ('beginner', 'intermediate', 'advanced')",
            name="ck_users_reading_level",
        ),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "books",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("cover_url", sa.Text()),
        sa.Column("isbn", sa.Text()),
        sa.Column("publication_year", sa.Integer()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("genres", postgresql.ARRAY(sa.Text())),
        sa.Column("moods", postgresql.JSONB()),
        sa.Column("reading_level", sa.Text()),
        sa.Column("average_rating", sa.Numeric(3, 2)),
        sa.Column("rating_count", sa.Integer(), server_default="0"),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(DIM)),
        sa.Column("source_ids", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_books_isbn", "books", ["isbn"])
    op.create_index("idx_books_title_author", "books", ["title", "author"])
    op.create_index("idx_books_genres", "books", ["genres"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX idx_books_title_fts ON books USING gin (to_tsvector('english', title))"
    )
    op.execute(
        "CREATE INDEX idx_books_author_fts ON books USING gin (to_tsvector('english', author))"
    )
    # ivfflat is deliberately NOT created here — see 0002.

    op.create_table(
        "user_book_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text()),
        sa.Column("rating", sa.Numeric(3, 2)),
        sa.Column("review_text", sa.Text()),
        sa.Column("started_reading_at", sa.DateTime(timezone=True)),
        sa.Column("finished_reading_at", sa.DateTime(timezone=True)),
        sa.Column("interaction_count", sa.Integer(), server_default="0"),
        sa.Column("total_time_spent", sa.Integer(), server_default="0"),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_interaction_user_book"),
        sa.CheckConstraint(
            "status IN ('want_to_read', 'currently_reading', 'read', 'abandoned')",
            name="ck_interactions_status",
        ),
        sa.CheckConstraint("rating >= 0 AND rating <= 5", name="ck_interactions_rating"),
    )
    op.create_index("idx_interactions_user_status", "user_book_interactions", ["user_id", "status"])
    op.create_index("idx_interactions_book_user", "user_book_interactions", ["book_id", "user_id"])

    op.create_table(
        "reading_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_page", sa.Integer(), server_default="0"),
        sa.Column("percentage", sa.Numeric(5, 2), server_default="0"),
        sa.Column("reading_speed", sa.Numeric(5, 2)),
        sa.Column("last_read_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("total_time_spent", sa.Integer(), server_default="0"),
        sa.Column("bookmarks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("notes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("device_type", sa.Text()),
        sa.Column("sync_version", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_progress_user_book"),
    )
    op.create_index("idx_progress_user", "reading_progress", ["user_id", "last_read_at"])

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), server_default="0"),
        sa.Column("factor_contributions", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(NOW() + INTERVAL '24 hours')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "book_id", name="uq_recommendation_user_book"),
    )
    op.create_index("idx_recommendations_user", "recommendations", ["user_id", "generated_at"])
    op.create_index("idx_recommendations_expires", "recommendations", ["expires_at"])

    op.create_table(
        "book_playlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playlist_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_playlist_id", sa.Text()),
        sa.Column("playlist_url", sa.Text()),
        sa.Column("tracks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("mood_match_score", sa.Numeric(5, 2)),
        sa.Column("genre_match_score", sa.Numeric(5, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "source", name="uq_playlist_book_source"),
        sa.CheckConstraint(
            "source IN ('spotify', 'youtube_music', 'custom')", name="ck_playlists_source"
        ),
    )

    op.create_table(
        "saved_playlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playlist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["playlist_id"], ["book_playlists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "playlist_id", name="uq_saved_playlist"),
    )

    op.create_table(
        "user_preference_embeddings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(DIM)),
        sa.Column("short_term_embedding", pgvector.sqlalchemy.Vector(DIM)),
        sa.Column("long_term_embedding", pgvector.sqlalchemy.Vector(DIM)),
        sa.Column("preference_weights", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "system_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_metrics_name_time", "system_metrics", ["metric_name", "timestamp"])

    op.create_table(
        "recommendation_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feedback_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "feedback_type IN ('like', 'dislike', 'not_interested', 'already_read')",
            name="ck_feedback_type",
        ),
    )
    op.create_index("idx_feedback_user", "recommendation_feedback", ["user_id", "created_at"])


def downgrade() -> None:
    for table in (
        "recommendation_feedback",
        "system_metrics",
        "user_preference_embeddings",
        "saved_playlists",
        "book_playlists",
        "recommendations",
        "reading_progress",
        "user_book_interactions",
        "books",
        "users",
    ):
        op.drop_table(table)
