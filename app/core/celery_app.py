"""Celery application and beat schedule."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "booktunes",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    # Restart the worker process periodically — the embedding model leaks a
    # little memory per batch and a free dyno has no headroom to absorb it.
    worker_max_tasks_per_child=50,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,          # free tier: one worker, one process
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=60,
    task_retry_backoff=True,
    task_retry_backoff_max=600,
    task_retry_jitter=True,
    # Results are only used for debugging; expiring them keeps Redis command
    # and key counts inside Upstash's 10k/day free allowance.
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    # Upstash closes idle connections aggressively.
    broker_transport_options={"visibility_timeout": 3600},
    redis_socket_keepalive=True,
)

# Jobs are staggered through the small hours so two memory-hungry tasks never
# overlap on a single free-tier worker.
celery_app.conf.beat_schedule = {
    "fetch-new-books": {
        "task": "app.tasks.fetch_new_books",
        "schedule": crontab(hour=2, minute=0),
    },
    "retrain-models-daily": {
        "task": "app.tasks.retrain_models",
        "schedule": crontab(hour=3, minute=0),
    },
    "update-recommendations": {
        "task": "app.tasks.update_recommendations",
        "schedule": crontab(hour=4, minute=0),
    },
    "generate-playlists": {
        "task": "app.tasks.generate_playlists",
        "schedule": crontab(hour=5, minute=0),
    },
    "update-user-preferences": {
        "task": "app.tasks.update_user_preferences",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "cleanup-cache": {
        "task": "app.tasks.cleanup_cache",
        "schedule": crontab(hour=23, minute=0),
    },
    "aggregate-metrics": {
        "task": "app.tasks.aggregate_metrics",
        "schedule": crontab(minute=0),
    },
    "weekly-usage-report": {
        "task": "app.tasks.weekly_usage_report",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),
    },
}
