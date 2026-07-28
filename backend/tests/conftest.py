"""Shared pytest fixtures.

The unit suite runs with no external services: Redis calls no-op when the
client is None (by design in ``redis_client``), and the embedding model is
stubbed so tests don't download 90MB or spend seconds on a forward pass.
"""

import os

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/booktunes_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    """Replace the real model with a deterministic hash-based encoder.

    Deterministic matters: similar inputs must produce stable vectors so
    similarity assertions don't flake between runs.
    """
    import hashlib

    import numpy as np

    from app.services.ai import embeddings

    def fake_encode(texts: list[str]) -> "np.ndarray":
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Tile the 32-byte digest out to 384 dims, then unit-normalise.
            raw = np.frombuffer(digest * 12, dtype=np.uint8)[:384].astype(np.float32)
            raw = (raw - 127.5) / 127.5
            vectors.append(raw / (np.linalg.norm(raw) or 1.0))
        return np.vstack(vectors).astype(np.float32)

    monkeypatch.setattr(embeddings, "_encode_sync", fake_encode)
    monkeypatch.setattr(embeddings, "_load_model", lambda: object())
    return fake_encode


@pytest.fixture
def anyio_backend():
    return "asyncio"
