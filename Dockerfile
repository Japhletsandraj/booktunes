# syntax=docker/dockerfile:1
#
# Multi-stage so the compiler toolchain needed by asyncpg/psycopg2 never ships
# in the runtime image. Matters here: Render's free tier builds slowly and the
# CPU-only torch wheel is already ~200MB.

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# The CPU wheel index is essential — the default torch wheel bundles CUDA and
# is ~2.5GB, which blows Render's free-tier image budget outright.
RUN pip install --no-cache-dir --prefix=/install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_CACHE_DIR=/app/.model_cache \
    MODEL_STORAGE_DIR=/app/.models \
    # Keep BLAS single-threaded: on a shared 0.1-CPU dyno, thread thrash makes
    # embedding *slower*, not faster.
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app

RUN useradd --create-home --uid 1000 booktunes \
    && mkdir -p /app/.model_cache /app/.models /app/logs \
    && chown -R booktunes:booktunes /app

COPY --chown=booktunes:booktunes . .

USER booktunes

# Bake the embedding model into the image. Without this, every cold start
# downloads ~90MB from HuggingFace before serving a single request — and
# Render free instances cold-start often.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/app/.model_cache')" \
    && echo "Embedding model cached"

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/ready || exit 1

# One worker: the embedding model is ~400MB resident, and a second worker
# would double that past the free tier's 512MB ceiling.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
