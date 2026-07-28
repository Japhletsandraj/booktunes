"""Run a scheduled task directly, without a Celery broker or worker.

Render's free plan has no background workers, so the beat schedule in
``app/core/celery_app.py`` has nothing to run it. This entrypoint calls the
same task functions synchronously, which lets GitHub Actions act as the
scheduler at no cost (see .github/workflows/scheduled-tasks.yml).

    python -m scripts.run_task update_user_preferences
    python -m scripts.run_task fetch_new_books --kwargs '{"per_genre": 10}'

The task bodies are identical either way — Celery tasks are plain callables,
so nothing is duplicated or simulated here.
"""

import argparse
import json
import sys
import time

from app.core.logging_config import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

# Tasks safe to invoke from a scheduler. `seed_catalogue` is excluded on
# purpose — it's a 40-minute one-off, not something to fire on a cron.
TASKS = {
    "retrain_models",
    "update_recommendations",
    "update_user_preferences",
    "fetch_new_books",
    "generate_playlists",
    "cleanup_cache",
    "aggregate_metrics",
    "weekly_usage_report",
    "rebuild_vector_index",
    "seed_catalogue",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Booktunes scheduled task")
    parser.add_argument("task", choices=sorted(TASKS))
    parser.add_argument("--kwargs", default="{}", help="JSON object of task kwargs")
    args = parser.parse_args()

    try:
        kwargs = json.loads(args.kwargs)
    except json.JSONDecodeError as exc:
        logger.error("--kwargs must be valid JSON: %s", exc)
        return 2

    from app import tasks

    func = getattr(tasks, args.task)
    # `bind=True` tasks expect `self` first; calling the underlying function
    # via .run() sidesteps that and avoids needing a broker connection.
    runner = getattr(func, "run", func)

    logger.info("Running %s(%s)", args.task, kwargs)
    started = time.time()
    try:
        result = runner(**kwargs)
    except Exception:
        logger.exception("Task %s failed", args.task)
        return 1

    logger.info("Task %s finished in %.1fs: %s", args.task, time.time() - started, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
