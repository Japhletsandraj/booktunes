"""Embedding helpers and vector maths."""

import numpy as np
import pytest

from app.services.ai import embeddings


def test_build_book_text_orders_high_signal_fields_first():
    """The model truncates long input, so title/genre must precede the blurb."""
    text = embeddings.build_book_text(
        title="Dune",
        author="Frank Herbert",
        description="X" * 5000,
        genres=["science_fiction", "adventure"],
        moods={"epic": 0.9, "tense": 0.4},
    )
    assert text.startswith("Dune by Frank Herbert")
    assert text.index("Genres:") < text.index("XXX")
    assert "epic" in text
    # Description is clipped, not passed whole.
    assert len(text) < 3000


def test_build_book_text_handles_missing_fields():
    text = embeddings.build_book_text("Untitled", "Anon")
    assert text == "Untitled by Anon"


def test_moods_ordered_by_strength():
    text = embeddings.build_book_text(
        "T", "A", None, None, {"cozy": 0.1, "epic": 0.9, "dark": 0.5}
    )
    assert text.index("epic") < text.index("dark") < text.index("cozy")


async def test_embed_text_returns_none_for_blank():
    assert await embeddings.embed_text("") is None
    assert await embeddings.embed_text("   ") is None


async def test_embed_text_is_deterministic():
    a = await embeddings.embed_text("The Hobbit", use_cache=False)
    b = await embeddings.embed_text("The Hobbit", use_cache=False)
    assert a == b
    assert len(a) == 384


async def test_embed_batch_preserves_positions():
    """Blank entries must map to None *in place*, or callers zip the wrong
    vector onto the wrong book."""
    results = await embeddings.embed_batch(["alpha", "", "beta"])
    assert len(results) == 3
    assert results[1] is None
    assert results[0] is not None and results[2] is not None
    assert results[0] != results[2]


async def test_embed_batch_empty_input():
    assert await embeddings.embed_batch([]) == []


def test_cosine_similarity_bounds():
    a = [1.0, 0.0, 0.0]
    assert embeddings.cosine_similarity(a, a) == pytest.approx(1.0)
    assert embeddings.cosine_similarity(a, [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)
    assert embeddings.cosine_similarity(a, [0.0, 1.0, 0.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_is_safe():
    assert embeddings.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_weighted_average_is_unit_length():
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    result = embeddings.weighted_average(vectors, [1.0, 1.0])
    assert np.linalg.norm(result) == pytest.approx(1.0, abs=1e-5)


def test_negative_weights_push_away():
    """A disliked book must move the profile away from that region, which is
    what stops rejected books resurfacing."""
    liked = [1.0, 0.0]
    disliked = [0.0, 1.0]
    result = embeddings.weighted_average([liked, disliked], [1.0, -0.5])
    assert result[0] > 0
    assert result[1] < 0


def test_weighted_average_handles_cancellation():
    # Opposing vectors of equal weight leave no usable signal.
    assert embeddings.weighted_average([[1.0, 0.0], [-1.0, 0.0]], [1.0, 1.0]) is None


def test_weighted_average_empty_and_zero_weights():
    assert embeddings.weighted_average([], []) is None
    assert embeddings.weighted_average([[1.0, 0.0]], [0.0]) is None
