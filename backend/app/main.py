"""Booktunes API application entrypoint."""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import check_database, dispose_engine
from app.core.errors import register_exception_handlers
from app.core.logging_config import get_logger, request_id_ctx, setup_logging
from app.core.redis_client import check_redis, close_redis, init_redis
from app.utils.http import close_http_client

setup_logging()
logger = get_logger(__name__)

# --- Metrics -------------------------------------------------------------

REQUEST_COUNT = Counter(
    "booktunes_requests_total",
    "HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "booktunes_request_duration_seconds",
    "Request latency",
    ["method", "endpoint"],
    # Tuned around the 3s recommendation target from the spec.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0),
)
SERVICE_UP = Gauge(
    "booktunes_dependency_up", "Dependency reachable (1/0)", ["dependency"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Booktunes API (env=%s)", settings.ENVIRONMENT)

    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            # 10% sampling keeps us inside Sentry's free 5k-event month.
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        logger.info("Sentry initialised")

    db_ok = await check_database()
    SERVICE_UP.labels("database").set(1 if db_ok else 0)
    if not db_ok:
        # Deliberately non-fatal: Render restarts a crashed service in a loop,
        # and a booted API returning 503s is easier to debug than a crash loop.
        logger.error("Database unreachable at startup — /health will report degraded")

    await init_redis()
    redis_ok = await check_redis()
    SERVICE_UP.labels("redis").set(1 if redis_ok else 0)

    # Music clients are cheap to build and failures are cached, so probing here
    # surfaces bad credentials at deploy time rather than on first request.
    from app.services.music.music_service import MusicService

    music_status = MusicService.initialize_clients()
    for name, ok in music_status.items():
        SERVICE_UP.labels(name).set(1 if ok else 0)

    from app.services.ai.recommendation_engine import BookRecommendationEngine

    BookRecommendationEngine.initialize_models()

    # Loading the embedding model takes several seconds and ~90MB. Doing it
    # here means the first user request doesn't pay for it.
    try:
        from app.services.ai import embeddings

        embeddings.warm_up()
        SERVICE_UP.labels("embedding_model").set(1)
    except Exception as exc:
        SERVICE_UP.labels("embedding_model").set(0)
        logger.error("Embedding model failed to load: %s", exc)

    logger.info("Startup complete")
    yield

    logger.info("Shutting down")
    await close_http_client()
    await close_redis()
    await dispose_engine()


app = FastAPI(
    title="Booktunes API",
    version="1.0.0",
    description=(
        "AI-powered book discovery with matched music playlists.\n\n"
        "Authenticate via `POST /api/v1/auth/login`, then send "
        "`Authorization: Bearer <access_token>`."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

register_exception_handlers(app)

# --- Middleware ----------------------------------------------------------
# Order matters: middleware added last runs first. TrustedHost must run before
# anything that trusts the Host header.

if settings.ALLOWED_HOSTS and settings.ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    # Never "*" with allow_credentials=True — browsers reject that combination,
    # and it would expose authenticated endpoints to any origin.
    allow_origins=settings.CORS_ORIGINS,
    # Covers Vercel's per-deployment origins, which change on every push and so
    # cannot be listed above. Empty string must become None — Starlette compiles
    # whatever it is given, and "" fullmatches only the empty string but still
    # costs a regex call on every request.
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
    max_age=3600,
)


@app.middleware("http")
async def observability(request: Request, call_next):
    """Assign a request id, time the request, and record metrics."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()

    try:
        response: Response = await call_next(request)
        status_code = response.status_code
    except Exception:
        # The exception handlers build the body; we only need the metric here.
        REQUEST_COUNT.labels(request.method, request.url.path, "500").inc()
        request_id_ctx.reset(token)
        raise

    duration = time.perf_counter() - started

    # Use the route template, not the raw path — otherwise every book id
    # becomes its own Prometheus label and the series count explodes.
    route = request.scope.get("route")
    endpoint = getattr(route, "path", request.url.path)

    REQUEST_COUNT.labels(request.method, endpoint, str(status_code)).inc()
    REQUEST_LATENCY.labels(request.method, endpoint).observe(duration)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{duration * 1000:.1f}"

    if duration > 3.0:
        logger.warning(
            "Slow request %s %s took %.2fs", request.method, endpoint, duration
        )

    request_id_ctx.reset(token)
    return response


app.include_router(api_router, prefix="/api/v1")


# --- Operational endpoints ----------------------------------------------

@app.get("/", tags=["Meta"], summary="Service banner")
async def root():
    return {
        "service": "Booktunes API",
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
    }


@app.get("/health", tags=["Meta"], summary="Health check")
async def health_check(response: Response):
    """Liveness + dependency status.

    Returns 200 while the API can serve *any* traffic and 503 only when the
    database is down, because Render pulls the instance out of rotation on a
    non-200 — losing Redis or Spotify degrades features but shouldn't take the
    whole service offline.
    """
    database = await check_database()
    redis = await check_redis()

    from app.services.ai import embeddings
    from app.services.music.music_service import MusicService

    dependencies = {
        "database": database,
        "redis": redis,
        "embedding_model": embeddings.is_loaded(),
        **MusicService.health(),
    }
    for name, ok in dependencies.items():
        SERVICE_UP.labels(name).set(1 if ok else 0)

    healthy = database
    degraded = [name for name, ok in dependencies.items() if not ok]

    if not healthy:
        response.status_code = 503

    return {
        "status": "healthy" if healthy and not degraded
        else "degraded" if healthy else "unhealthy",
        "dependencies": dependencies,
        "degraded": degraded,
        "environment": settings.ENVIRONMENT,
        "timestamp": time.time(),
    }


@app.get("/health/ready", tags=["Meta"], summary="Readiness probe")
async def readiness(response: Response):
    """Stricter than /health — used by UptimeRobot to catch a half-booted app."""
    ready = await check_database()
    if not ready:
        response.status_code = 503
    return {"ready": ready}


@app.get("/metrics", tags=["Meta"], summary="Prometheus metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
