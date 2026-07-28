-- ===========================================================================
-- Booktunes — full schema for Supabase / PostgreSQL 15+
--
-- Run this in the Supabase SQL editor for a one-shot setup, or use Alembic
-- (`alembic upgrade head`) which applies the same DDL as a versioned migration.
-- The two are kept in sync by hand; Alembic is authoritative for deploys.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- --------------------------------------------------------------------------
-- Tables
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    avatar_url TEXT,
    reading_level TEXT CHECK (reading_level IN ('beginner', 'intermediate', 'advanced')),
    join_date TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS books (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    description TEXT,
    cover_url TEXT,
    isbn TEXT,
    publication_year INTEGER,
    page_count INTEGER,
    genres TEXT[],
    moods JSONB,
    reading_level TEXT,
    average_rating DECIMAL(3,2),
    rating_count INTEGER DEFAULT 0,
    embedding vector(384),
    source_ids JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_book_interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    status TEXT CHECK (status IN ('want_to_read', 'currently_reading', 'read', 'abandoned')),
    rating DECIMAL(3,2) CHECK (rating >= 0 AND rating <= 5),
    review_text TEXT,
    started_reading_at TIMESTAMPTZ,
    finished_reading_at TIMESTAMPTZ,
    interaction_count INTEGER DEFAULT 0,
    total_time_spent INTEGER DEFAULT 0,
    last_interaction_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Deviation from the original spec: one row per (user, book). The library
    -- is a set, not an append-only log, so status changes update in place.
    CONSTRAINT uq_interaction_user_book UNIQUE (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS reading_progress (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    current_page INTEGER DEFAULT 0,
    percentage DECIMAL(5,2) DEFAULT 0,
    reading_speed DECIMAL(5,2),
    last_read_at TIMESTAMPTZ DEFAULT NOW(),
    total_time_spent INTEGER DEFAULT 0,
    bookmarks JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    device_type TEXT,
    sync_version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_progress_user_book UNIQUE (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    match_score DECIMAL(5,2) NOT NULL,
    confidence_score DECIMAL(5,2) DEFAULT 0,
    factor_contributions JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    -- Deviation: the spec's UNIQUE(user_id, book_id, generated_at) permits
    -- unbounded duplicates because generated_at always differs. Keying on
    -- (user_id, book_id) lets regeneration upsert and keeps the table small,
    -- which matters on a 500MB free tier.
    CONSTRAINT uq_recommendation_user_book UNIQUE (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS book_playlists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    playlist_name TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL CHECK (source IN ('spotify', 'youtube_music', 'custom')),
    source_playlist_id TEXT,
    playlist_url TEXT,
    tracks JSONB NOT NULL DEFAULT '[]'::jsonb,
    mood_match_score DECIMAL(5,2),
    genre_match_score DECIMAL(5,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_playlist_book_source UNIQUE (book_id, source)
);

CREATE TABLE IF NOT EXISTS saved_playlists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id UUID NOT NULL REFERENCES book_playlists(id) ON DELETE CASCADE,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_saved_playlist UNIQUE (user_id, playlist_id)
);

CREATE TABLE IF NOT EXISTS user_preference_embeddings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    embedding vector(384),
    short_term_embedding vector(384),
    long_term_embedding vector(384),
    preference_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name TEXT NOT NULL,
    value DECIMAL NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL CHECK (
        feedback_type IN ('like', 'dislike', 'not_interested', 'already_read')
    ),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- --------------------------------------------------------------------------
-- Indexes
-- --------------------------------------------------------------------------

-- IMPORTANT: build the ivfflat index only AFTER seeding books. Building it on
-- an empty table produces useless centroids and silently degrades recall.
-- Rule of thumb: lists = rows/1000 (min 10). At ~1000 seeded books use 10-32,
-- not the 100 in the original spec, which would leave ~10 rows per list.
-- Re-run this after any large ingest:
--   DROP INDEX IF EXISTS idx_books_embedding;
--   CREATE INDEX ... WITH (lists = <rows/1000>);
CREATE INDEX IF NOT EXISTS idx_books_embedding ON books
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 32);

CREATE INDEX IF NOT EXISTS idx_books_genres ON books USING gin (genres);
CREATE INDEX IF NOT EXISTS idx_books_title_fts ON books
    USING gin (to_tsvector('english', title));
CREATE INDEX IF NOT EXISTS idx_books_author_fts ON books
    USING gin (to_tsvector('english', author));
CREATE INDEX IF NOT EXISTS idx_books_title_author ON books (title, author);
CREATE INDEX IF NOT EXISTS idx_books_isbn ON books (isbn);

CREATE INDEX IF NOT EXISTS idx_interactions_user_status
    ON user_book_interactions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_interactions_book_user
    ON user_book_interactions (book_id, user_id);

CREATE INDEX IF NOT EXISTS idx_recommendations_user
    ON recommendations (user_id, generated_at);
CREATE INDEX IF NOT EXISTS idx_recommendations_expires
    ON recommendations (expires_at);

CREATE INDEX IF NOT EXISTS idx_progress_user
    ON reading_progress (user_id, last_read_at);

CREATE INDEX IF NOT EXISTS idx_metrics_name_time
    ON system_metrics (metric_name, timestamp);

CREATE INDEX IF NOT EXISTS idx_feedback_user
    ON recommendation_feedback (user_id, created_at);

-- --------------------------------------------------------------------------
-- Row Level Security
--
-- CAVEAT: the policies below use auth.uid(), which is only populated when a
-- request carries a Supabase Auth JWT (i.e. the frontend talking to PostgREST
-- directly). This API issues its OWN JWTs and connects as the `postgres`
-- role, which BYPASSES RLS entirely. So these policies are defence-in-depth
-- for any direct Supabase client access — they are NOT what protects this
-- API's data. Per-user authorization is enforced in the endpoint layer by
-- filtering every query on the authenticated user_id.
--
-- If you want RLS to actually apply to this backend, connect as a non-
-- superuser role and set `SET LOCAL request.jwt.claims` per transaction.
-- --------------------------------------------------------------------------

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_book_interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE reading_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_playlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE recommendation_feedback ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own data" ON users;
CREATE POLICY "Users can view own data" ON users
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own data" ON users;
CREATE POLICY "Users can update own data" ON users
    FOR UPDATE USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can manage own interactions" ON user_book_interactions;
CREATE POLICY "Users can manage own interactions" ON user_book_interactions
    FOR ALL USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can manage own progress" ON reading_progress;
CREATE POLICY "Users can manage own progress" ON reading_progress
    FOR ALL USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can view own recommendations" ON recommendations;
CREATE POLICY "Users can view own recommendations" ON recommendations
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can manage own saved playlists" ON saved_playlists;
CREATE POLICY "Users can manage own saved playlists" ON saved_playlists
    FOR ALL USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can manage own feedback" ON recommendation_feedback;
CREATE POLICY "Users can manage own feedback" ON recommendation_feedback
    FOR ALL USING (auth.uid() = user_id);

-- Books and playlists are public catalogue data — readable by anyone.
