# Booktunes API

AI-powered book discovery with matched music playlists. Backend only — no frontend.

FastAPI + PostgreSQL/pgvector + Sentence Transformers, designed to run entirely on
free tiers.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Manual setup you must do](#manual-setup-you-must-do)
- [Deployment](#deployment)
- [API reference](#api-reference)
- [How the recommender works](#how-the-recommender-works)
- [Scheduled jobs](#scheduled-jobs)
- [Staying inside the free tiers](#staying-inside-the-free-tiers)
- [Testing](#testing)
- [Known constraints](#known-constraints)
- [Deviations from the original spec](#deviations-from-the-original-spec)

---

## What it does

- Ingests books from **Open Library** (primary) and **Google Books** (fallback /
  metadata backfill), embedding each one with `all-MiniLM-L6-v2`.
- Recommends books per user via a **hybrid** of pgvector content similarity and
  an **ALS** collaborative-filtering model, scored across **seven weighted
  factors** and explained in plain language.
- Generates a **playlist per book** from Spotify (primary) or YouTube Music
  (fallback), driven by genre and inferred mood.
- Tracks **reading progress** with versioned, conflict-detecting cross-device
  sync on a 30-second poll.
- Learns **user taste vectors** (short-term + long-term) from weighted
  interactions, refreshed every 6 hours.

---

## Architecture

```
app/
├── main.py                  FastAPI app: lifespan, middleware, metrics, health
├── tasks.py                 Celery tasks (also callable without a broker)
├── core/
│   ├── config.py            Settings (pydantic-settings)
│   ├── database.py          Lazy async engine + session helpers
│   ├── redis_client.py      Cache wrapper; degrades to no-op if Redis is down
│   ├── security.py          bcrypt + JWT
│   ├── errors.py            Typed errors and the shared response envelope
│   ├── logging_config.py    JSON logs in prod, request-id correlation
│   └── celery_app.py        Celery config + beat schedule
├── models/models.py         SQLAlchemy models (pgvector columns)
├── schemas/                 Pydantic request/response models
├── api/
│   ├── deps.py              Auth, DB session, per-route rate limiting
│   └── v1/endpoints/        auth, books, recommendations, reading,
│                            playlists, users, library
├── services/
│   ├── book_ingestion.py    Open Library + Google Books pipeline
│   ├── reading_service.py   Progress, sync, streaks, stats
│   ├── cost_monitor.py      Free-tier quota tracking + alerts
│   ├── ai/
│   │   ├── embeddings.py            Sentence-Transformers wrapper
│   │   ├── als.py                   ALS matrix factorisation (numpy)
│   │   ├── preference_learner.py    User taste vectors
│   │   └── recommendation_engine.py Hybrid ranking + explanations
│   └── music/
│       ├── genre_mapping.py         Book genre/mood → music mappings
│       └── music_service.py         Spotify + YouTube Music
└── utils/                   HTTP retry, rate limiter, genre/mood taxonomy

migrations/                  Alembic (authoritative schema)
sql/schema.sql               Same DDL, for one-shot setup in Supabase's editor
scripts/                     seed_books, rebuild_vector_index, run_task
tests/                       160 tests, no external services required
```

**Request path:** client → CORS/GZip/TrustedHost → observability middleware
(request id + Prometheus) → router → `get_current_user` → service → SQLAlchemy /
Redis / external API.

---

## Quick start

### With Docker (nothing to sign up for)

```bash
git clone https://github.com/yourusername/booktunes.git
cd booktunes

cp .env.example .env      # Spotify keys optional; see "Manual setup" below

docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_books --genres fantasy,mystery --limit 40
docker compose exec api python -m scripts.rebuild_vector_index
```

`docker-compose.yml` runs Postgres with pgvector and Redis locally, so the only
env vars that matter are the optional third-party keys. In production those two
are Supabase and Upstash instead.

### Without Docker

Requires **Python 3.11** (3.12 works; 3.13 has no wheels for some pinned deps).

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate

# The CPU torch index matters — the default wheel bundles CUDA (~2.5GB).
pip install -r requirements-dev.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu

cp .env.example .env            # then fill it in — see the next section
alembic upgrade head            # creates every table
python -m scripts.seed_books    # ~1000 books, 20-40 min (rate-limited)
python -m scripts.rebuild_vector_index   # IMPORTANT — see note below
uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs>.

> **Rebuild the vector index after seeding.** An `ivfflat` index derives its
> centroids from the rows present when it is *built*. Built against an empty
> table it still returns results — just measurably worse ones, silently. Re-run
> `scripts/rebuild_vector_index.py` after any large ingest.

A short smoke test:

```bash
curl -s localhost:8000/health | jq

TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","email":"demo@example.com","password":"demopass1"}' \
  | jq -r .access_token)

curl -s -X POST localhost:8000/api/v1/auth/preferences \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"favorite_genres":["fantasy","science_fiction"],"preferred_moods":["epic"]}' | jq

curl -s "localhost:8000/api/v1/recommendations/personalized?limit=5" \
  -H "Authorization: Bearer $TOKEN" | jq
```

---

## Manual setup you must do

Everything below has a free tier. None require a card.

### 1. Supabase (database) — required in production

1. Create a project at <https://supabase.com/dashboard>.
2. **SQL Editor** → run `CREATE EXTENSION IF NOT EXISTS vector;`
   (Or run all of `sql/schema.sql` to skip Alembic entirely.)
3. **Settings → Database → Connection string → URI** → `DATABASE_URL`.

   Use the **session pooler** host on port **5432**. The transaction pooler
   (6543) does not support prepared statements, which asyncpg relies on.

### 2. Upstash (Redis) — recommended

1. Create a Redis database at <https://console.upstash.com>.
2. Copy the `redis://` (or `rediss://`) URL → `REDIS_URL`.

Optional but strongly advised: without Redis, caching and rate limiting silently
no-op and every recommendation is recomputed from scratch. The app still works.

### 3. Spotify — recommended

1. <https://developer.spotify.com/dashboard> → **Create app**.
2. Copy Client ID / Secret → `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`.
3. No redirect URI needed — this uses Client Credentials.

> **Read this before debugging playlists.** Spotify deprecated
> `/recommendations`, `/audio-features` and related-artists on **2024-11-27**
> for apps created after that date. Apps created since then get **403** from
> those endpoints. Search still works for everyone, so the service is built
> around *mood-and-genre-driven search* and treats the recommendations endpoint
> as an opportunistic bonus. If your app predates the cutoff you additionally
> get audio-feature targeting for free. Nothing needs configuring either way.

Without Spotify credentials the service falls back to YouTube Music, which needs
no setup at all.

### 4. Optional services

| Variable | Where to get it | If unset |
|---|---|---|
| `GOOGLE_BOOKS_API_KEY` | Google Cloud Console → Credentials → API key | Falls back to unauthenticated (~1000 req/day/IP) |
| `CLOUDINARY_URL` | Cloudinary dashboard → API Environment variable | Covers served from source CDNs (fine, and free) |
| `RESEND_API_KEY` + `ALERT_EMAIL_TO` | <https://resend.com/api-keys> | No weekly usage email |
| `ALERT_WEBHOOK_URL` | Discord → Server Settings → Integrations → Webhooks | Quota alerts only go to logs |
| `SENTRY_DSN` | <https://sentry.io> project settings | No error tracking |

### 5. Generate a secret key

```bash
openssl rand -hex 32     # → SECRET_KEY
```

---

## Deployment

### Render (free)

1. Push to GitHub.
2. Render → **New → Blueprint** → point at this repo. `render.yaml` provisions
   one free web service.
3. Set the `sync: false` env vars in the dashboard (`DATABASE_URL`, `REDIS_URL`,
   Spotify credentials, …).
4. Run migrations once from your machine against the production URL:
   `DATABASE_URL=<prod> alembic upgrade head`

**What "free" actually covers on Render** — worth knowing up front:

| | Free? |
|---|---|
| Web service | Yes — 750 hours/month, sleeps after 15 min idle, ~50s cold start |
| **Background worker** | **No — paid plans only** |
| **Cron job** | **No — paid plans only** |
| Postgres | 30-day expiry (hence Supabase) |

Because free Render has no worker, the Celery beat schedule has nothing to run
it. **Scheduled work runs on GitHub Actions instead** — see below. The paid
worker/beat services are in `render.yaml`, commented out, for later.

### Keeping the service awake

Free instances sleep after 15 minutes. Point **UptimeRobot** (free, 50 monitors)
at `https://<your-app>.onrender.com/health/ready` on a 5-minute interval. 750
hours/month is under a full month (~730h), so a single always-on service fits.

---

## API reference

Interactive docs at `/docs`. All authenticated routes take
`Authorization: Bearer <access_token>`.

### Authentication
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/auth/register` | Returns a token pair |
| POST | `/api/v1/auth/login` | Accepts username **or** email in `identifier` |
| POST | `/api/v1/auth/refresh` | Rotate an expiring access token |
| POST | `/api/v1/auth/logout` | Revokes the token's `jti` (needs Redis) |
| POST | `/api/v1/auth/preferences` | Cold-start quiz; seeds a taste vector immediately |
| GET | `/api/v1/auth/me` | Current profile |

### Books
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/books` | Filters + `semantic=true` for embedding search |
| GET | `/api/v1/books/trending` | Recent in-app activity, falls back to damped rating |
| GET | `/api/v1/books/genres` · `/moods` | Controlled vocabularies |
| GET | `/api/v1/books/genre/{genre}` | Paginated |
| GET | `/api/v1/books/mood/{mood}` | Ranked by mood strength |
| GET | `/api/v1/books/{book_id}` | Includes similar books + your status if signed in |

### Recommendations
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/recommendations/personalized` | `?refresh=true` bypasses cache |
| GET | `/api/v1/recommendations/book/{book_id}/summary` | Seven-factor explanation |
| GET | `/api/v1/recommendations/mood/{mood}` | Mood-filtered, then personally ranked |
| POST | `/api/v1/recommendations/feedback` | like / dislike / not_interested / already_read |

### Reading
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/reading/currently` | With estimated time remaining |
| POST | `/api/v1/reading/progress` | Send `base_version` for conflict detection |
| GET | `/api/v1/reading/progress/{book_id}` | `?from_version=` returns a delta |
| POST | `/api/v1/reading/batch-sync` | Up to 10 books; per-item success/failure |
| GET | `/api/v1/reading/stats` · `/streak` · `/sync-config` | |

### Playlists
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/playlists/book/{book_id}` | Generates on demand if absent |
| POST | `/api/v1/playlists/generate` | `force_regenerate` to rebuild |
| POST | `/api/v1/playlists/save` · DELETE `/save/{id}` | |
| GET | `/api/v1/playlists/user` | Your saved playlists |
| POST | `/api/v1/playlists/preview` | 30s clips — frequently unavailable, see below |

### Library
`GET` / `POST` `/api/v1/library`, `PUT` / `DELETE` `/api/v1/library/{book_id}`.

### Users
`/api/v1/users/me` (GET, PATCH, DELETE), `/me/stats`, `/me/taste`,
`POST /me/refresh-preferences`.

### Error format

Every error shares one envelope:

```json
{
  "error": {
    "code": "book_not_found",
    "message": "No book with id ...",
    "details": {}
  },
  "request_id": "a1b2c3d4e5f6"
}
```

`request_id` is echoed in the `X-Request-ID` header and appears in every log
line for that request.

---

## How the recommender works

**1 — User vector.** Each interaction becomes a weighted signal (finished 1.0,
rated 4-5 0.8, … abandoned −0.5, disliked −0.6). Negative weights genuinely push
the vector *away* from a region — dropping them is why naive recommenders keep
resurfacing rejected books. Signals decay exponentially: 7-day half-life for the
short-term vector, 180-day for the long-term. The two are blended, with the
short-term share scaling from 0.3 → 0.7 as evidence accumulates.

**2 — Candidates.** Content-based comes from pgvector (`<=>` cosine distance,
ordered in-database so the index does the work). Collaborative comes from ALS.

**3 — Blend.** Adaptive, not fixed:

| Situation | Collaborative | Content |
|---|---|---|
| No trained model | 0% | 100% |
| New user | 0% | 100% |
| Established user | up to 50% | ≥50% |

CF is capped at 50% deliberately — content similarity stays the anchor. With no
signal at all, it falls back to Bayesian-damped popularity so a single 5-star
book with one vote can't outrank a 4.5-star book with a thousand.

**4 — Seven factors** (weights sum to 1.0, asserted by a test):

| Factor | Weight |
|---|---|
| Similarity to enjoyed books | 20% |
| Genre match | 20% |
| Author affinity | 15% |
| Mood alignment | 15% |
| Reading level | 10% |
| Playlist synergy | 10% |
| Time commitment | 10% |

Unknown values score ~50 (neutral) rather than 0 — a missing page count is not
the same as a bad page count.

**5 — Diversity.** Greedy re-rank caps repeats at 2 books per author and 40% per
genre, with deferred candidates backfilling so the shelf is never short.

**6 — Explanation.** Template-based, assembled from the top-contributing factors
plus the weakest one as a caveat, and it discloses low confidence explicitly.
Deliberately not LLM-generated: free, instant, and incapable of inventing a
reason that isn't in the scores.

---

## Scheduled jobs

Defined once in `app/core/celery_app.py` and runnable two ways:

**With a Celery worker** (paid Render, or `docker compose --profile workers up`):
```bash
celery -A app.core.celery_app worker --loglevel=info --concurrency=1
celery -A app.core.celery_app beat  --loglevel=info
```

**Without a broker** — the free path, used by GitHub Actions:
```bash
python -m scripts.run_task update_user_preferences
```
Both invoke the same task functions; nothing is duplicated.

| Task | Schedule | Does |
|---|---|---|
| `fetch_new_books` | 02:00 daily | Tops up the catalogue |
| `retrain_models` | 03:00 daily | Retrains + hot-swaps ALS |
| `update_recommendations` | 04:00 daily | Precomputes for active users |
| `generate_playlists` | 05:00 daily | Playlists for books lacking one |
| `update_user_preferences` | every 6h | Rebuilds taste vectors (delta only) |
| `cleanup_cache` | 23:00 daily | Prunes expired rows |
| `aggregate_metrics` | hourly | Samples quotas, alerts on breach |
| `weekly_usage_report` | Mon 09:00 | Emails a usage digest |

`.github/workflows/scheduled-tasks.yml` maps each cron to its task. Add the
repo secrets listed at the top of that file. Manual runs via **Actions →
Scheduled tasks → Run workflow**.

> GitHub disables scheduled workflows in repos inactive for 60 days. If the
> crons go quiet, re-enable them in the Actions tab.

---

## Staying inside the free tiers

`CostMonitor` samples usage hourly into `system_metrics` and alerts once per
metric per day at 80% of any ceiling.

| Resource | Limit | Guard |
|---|---|---|
| Supabase Postgres | 500 MB | Expired recommendations and 30-day-old metrics pruned nightly |
| Upstash Redis | 10k commands/day | Every cache op counted; TTLs on everything |
| Cloudinary | 25 GB | Covers hotlinked from source by default (0 usage) |
| Render | 750 h/month | One service; UptimeRobot keeps it warm |
| GitHub Actions | 2000 min/month | Jobs use ~200-400 min |
| Resend | 100 emails/day | One weekly digest |

Set `ALERT_WEBHOOK_URL` to a free Discord webhook to actually receive alerts —
otherwise they only reach the logs.

---

## Testing

```bash
pytest                        # 160 tests, no external services needed
pytest --cov=app
ruff check app scripts tests
```

The suite stubs the embedding model with a deterministic hash-based encoder, so
it runs in seconds without downloading 90MB or doing a real forward pass. Tests
that need real persistence are marked `integration` and run in CI against a
`pgvector/pgvector:pg16` service container.

`tests/test_api_contract.py` deliberately injects a database session that raises
on any attribute access — so a test can never silently start depending on real
persistence and pass by accident.

---

## Known constraints

These are properties of the free tiers and upstream APIs, not bugs:

- **~50s cold start.** Render free instances sleep after 15 min idle, and the
  embedding model has to load. The model is baked into the Docker image so it
  isn't also downloading 90MB. UptimeRobot avoids most of this.
- **Spotify previews are frequently `null`.** Spotify has been withdrawing
  30-second preview URLs, and availability varies by market. `available: false`
  is a routine response, not an error.
- **Spotify recommendations/audio-features 403 for apps created after
  2024-11-27.** Handled — see [Spotify setup](#3-spotify--recommended).
- **The trained ALS model is stored on ephemeral disk.** It's lost on redeploy
  and retrained nightly. If you split the worker into its own Render service,
  attach a shared Disk or push the `.npz` to object storage, or the web service
  will never see the worker's models.
- **RLS policies don't protect this API.** The `auth.uid()` policies in
  `sql/schema.sql` only apply to requests carrying a Supabase Auth JWT. This API
  issues its own JWTs and connects as `postgres`, which *bypasses RLS*.
  Per-user authorization is enforced in the endpoint layer, where every query
  filters on the authenticated `user_id`. The policies are defence-in-depth for
  direct Supabase client access.
- **Rate limiting is per-instance.** Fine at one instance; move the token bucket
  into Redis before scaling out.

---

## Deviations from the original spec

Each of these is a deliberate correction, not an oversight:

1. **`recommendations` is keyed `UNIQUE(user_id, book_id)`**, not
   `(user_id, book_id, generated_at)`. Including a timestamp makes the
   constraint vacuous — every regeneration has a new `generated_at`, so rows
   accumulate without bound. On a 500MB budget that's the fastest way to run out
   of database.
2. **`user_book_interactions` is `UNIQUE(user_id, book_id)`.** A library is a
   set, not an append-only log; status changes update in place.
3. **The `ivfflat` index is a separate migration (0002) with `lists=32`, not
   `lists=100` in the initial schema.** pgvector's guidance is roughly
   `rows/1000`; at ~1000 books, 100 lists leaves ~10 rows each and recall
   degrades. Built on an empty table it's worse still, so it's split out and
   rebuildable.
4. **Dependency versions bumped.** `sentence-transformers==2.2.2` is broken
   against current `huggingface-hub` (it imports the removed `cached_download`);
   `pgvector==0.2.0` predates SQLAlchemy 2.0 support. `bcrypt` is pinned `<4.1`
   because passlib 1.7.4 is incompatible with it.
5. **Celery beat is not the only scheduler.** Render's free plan has no
   background workers, so `scripts/run_task.py` + GitHub Actions provides the
   genuinely-free path. "Zero cost is non-negotiable" was taken literally.
6. **The AI summary is template-based, not generated.** An LLM call per
   recommendation is neither free nor fast, and would be able to invent reasons
   the factor scores don't support.
7. **No WebSocket endpoint.** The spec listed it as optional. Free instances
   sleep and drop connections, which makes a 30-second poll strictly more
   reliable here — and `/reading/sync-config` tells clients how to poll.
8. **`GZipMiddleware` is added before CORS** so compression doesn't strip CORS
   headers, and `allow_origins` is never `*` alongside `allow_credentials`
   (browsers reject that pairing outright).

---

## Licence

MIT.
