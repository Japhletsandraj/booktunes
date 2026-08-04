### Live: [click here](https://booktunes-murex.vercel.app/)

# BookTunes

AI-powered book discovery with matched music playlists.

## Repository layout

```
booktunes/
├── backend/     FastAPI + PostgreSQL/pgvector + Sentence Transformers API
└── frontend/    (not built yet)
```

Everything currently lives in [`backend/`](backend/) — including its own
`Dockerfile`, `docker-compose.yml`, `render.yaml`, Alembic migrations and test
suite. Run every backend command from inside that directory.

## Quick start

```bash
cd backend
cp .env.example .env      # then fill in the secrets
make dev                  # install runtime + dev dependencies
make up                   # Postgres (pgvector) + Redis + API via Docker
make migrate              # apply schema
make seed                 # populate the book catalogue
```

The API is then on <http://localhost:8000>, docs at `/docs`.

See [`backend/README.md`](backend/README.md) for architecture, the API
reference, how the recommender scores books, the scheduled-job setup, and the
free-tier deployment notes.

## CI

Workflows live in `.github/workflows/` at the repo root and run against
`backend/`:

- `ci.yml` — lint, migrations, tests and a Docker build on every push and PR.
- `scheduled-tasks.yml` — cron-driven background jobs (Render's free plan has
  no workers, so GitHub Actions is the scheduler).
