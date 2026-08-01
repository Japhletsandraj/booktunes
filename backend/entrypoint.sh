#!/bin/sh
# Apply migrations, then serve.
#
# Nothing else runs Alembic against the deployed database. Render's free plan
# has no release phase and no shell, the paid worker/cron services that could
# have carried it are commented out in render.yaml, and the GitHub Actions
# workflow that replaces them only runs scheduled tasks. So the schema was
# never created: /health/ready passed (it only does SELECT 1) while every
# endpoint touching a table returned 503 database_error.
#
# `upgrade head` is a no-op once the revision matches, so paying this on every
# cold start costs one round-trip on the free tier's frequent restarts.
set -e

echo "entrypoint: applying migrations"
alembic upgrade head
echo "entrypoint: migrations at head, starting server"

# exec so uvicorn replaces this shell as PID 1 and receives Render's SIGTERM
# directly — otherwise shutdown waits for the 30s kill timeout on every deploy.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
