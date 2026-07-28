"""Sentence-Transformer embedding generation.

The model is a process-wide singleton loaded lazily: importing this module must
stay cheap so Celery's `--beat` process and test collection don't pay ~90MB of
model load they never use.

Encoding is CPU-bound and holds the GIL, so every public call hops to a thread
via ``asyncio.to_thread`` — otherwise a single batch encode stalls the whole
event loop and every concurrent request times out.
"""

import asyncio
import hashlib
import threading
from collections.abc import Sequence

import numpy as np

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set

logger = get_logger(__name__)

_model = None
_model_lock = threading.Lock()

EMBEDDING_CACHE_TTL = 60 * 60 * 24 * 7  # a book's text rarely changes
_CACHE_PREFIX = "emb:v1:"


def _load_model():
    """Load (or return) the shared SentenceTransformer."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # another thread won the race
            return _model
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s", settings.EMBEDDING_MODEL)
        model = SentenceTransformer(
            settings.EMBEDDING_MODEL,
            cache_folder=settings.EMBEDDING_CACHE_DIR,
            device="cpu",
        )
        dim = model.get_sentence_embedding_dimension()
        if dim != settings.EMBEDDING_DIM:
            # The vector columns are fixed at 384; a mismatched model would
            # fail on insert with an opaque pgvector error. Fail loudly here.
            raise RuntimeError(
                f"{settings.EMBEDDING_MODEL} produces {dim}-dim vectors but the "
                f"schema expects {settings.EMBEDDING_DIM}. Change EMBEDDING_MODEL "
                f"or migrate the vector columns."
            )
        _model = model
        logger.info("Embedding model ready (%d dims)", dim)
    return _model


def warm_up() -> None:
    """Preload the model. Called from the app lifespan so the first real
    request doesn't eat the several-second load."""
    _load_model()


def is_loaded() -> bool:
    return _model is not None


def build_book_text(
    title: str,
    author: str,
    description: str | None = None,
    genres: Sequence[str] | None = None,
    moods: dict | None = None,
) -> str:
    """Flatten a book's metadata into the string we embed.

    Order matters: the model truncates at 256 word-pieces, so the highest-signal
    fields go first and the (often long, often boilerplate) description last.
    """
    parts = [f"{title} by {author}"]
    if genres:
        parts.append("Genres: " + ", ".join(genres[:8]))
    if moods:
        top = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:5]
        if top:
            parts.append("Mood: " + ", ".join(name for name, _ in top))
    if description:
        parts.append(description[:2000])
    return ". ".join(parts)


def _cache_key(text: str) -> str:
    digest = hashlib.sha256(
        f"{settings.EMBEDDING_MODEL}:{text}".encode()
    ).hexdigest()[:32]
    return f"{_CACHE_PREFIX}{digest}"


def _encode_sync(texts: list[str]) -> np.ndarray:
    model = _load_model()
    vectors = model.encode(
        texts,
        batch_size=16,          # keeps peak RSS well under a 512MB dyno
        convert_to_numpy=True,
        normalize_embeddings=True,  # cosine similarity becomes a dot product
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


async def embed_text(text: str, use_cache: bool = True) -> list[float] | None:
    """Embed one string. Returns ``None`` for empty input."""
    text = (text or "").strip()
    if not text:
        return None

    key = _cache_key(text)
    if use_cache:
        cached = await cache_get(key)
        if cached:
            return cached

    try:
        vector = (await asyncio.to_thread(_encode_sync, [text]))[0].tolist()
    except Exception as exc:
        logger.error("Embedding failed: %s", exc)
        return None

    if use_cache:
        await cache_set(key, vector, ttl=EMBEDDING_CACHE_TTL)
    return vector


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed many strings in one forward pass.

    Empty strings map to ``None`` and are excluded from the batch rather than
    embedded as noise.
    """
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    pending_idx: list[int] = []
    pending_txt: list[str] = []

    for i, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text:
            continue
        cached = await cache_get(_cache_key(text))
        if cached:
            results[i] = cached
        else:
            pending_idx.append(i)
            pending_txt.append(text)

    if pending_txt:
        try:
            vectors = await asyncio.to_thread(_encode_sync, pending_txt)
            # strict=True: a length mismatch here would silently assign the
            # wrong vector to the wrong text, which is near-impossible to spot
            # downstream.
            for slot, text, vector in zip(pending_idx, pending_txt, vectors, strict=True):
                as_list = vector.tolist()
                results[slot] = as_list
                await cache_set(_cache_key(text), as_list, ttl=EMBEDDING_CACHE_TTL)
        except Exception as exc:
            logger.error("Batch embedding failed for %d texts: %s", len(pending_txt), exc)

    return results


# --- Vector maths --------------------------------------------------------

def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0 else vector / norm


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return 0.0 if denom == 0 else float(np.dot(va, vb) / denom)


def weighted_average(
    vectors: Sequence[Sequence[float]], weights: Sequence[float]
) -> list[float] | None:
    """Weighted mean of vectors, renormalised to unit length.

    Negative weights are meaningful here — a disliked book pushes the user
    vector *away* from that region of the space.
    """
    if not vectors:
        return None
    matrix = np.asarray(vectors, dtype=np.float32)
    w = np.asarray(weights, dtype=np.float32).reshape(-1, 1)
    total = float(np.abs(w).sum())
    if total == 0:
        return None
    combined = (matrix * w).sum(axis=0) / total
    norm = float(np.linalg.norm(combined))
    if norm == 0:
        # Weights cancelled out exactly — no usable signal.
        return None
    return (combined / norm).tolist()
