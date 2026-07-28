"""Logging setup: JSON in production, human-readable locally.

A ``request_id`` ContextVar is injected into every record so a single request's
lines can be correlated across modules and across the API/worker boundary.
"""

import json
import logging
import logging.handlers
import os
import sys
from contextvars import ContextVar

from app.core.config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything passed via `logger.info(..., extra={"foo": 1})`
        for key, value in record.__dict__.items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.addFilter(RequestIdFilter())
    if settings.is_production:
        stream.setFormatter(JsonFormatter())
    else:
        stream.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    root.addHandler(stream)

    # Rotating file handler. Render's filesystem is ephemeral, so this is
    # mainly useful for local runs and self-hosted deploys.
    if not settings.is_production:
        os.makedirs("logs", exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            "logs/booktunes.log", maxBytes=10 * 1024 * 1024, backupCount=3
        )
        file_handler.addFilter(RequestIdFilter())
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    # These are chatty at INFO and drown out everything else.
    for noisy in ("httpx", "httpcore", "urllib3", "sentence_transformers", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if level <= logging.DEBUG else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
