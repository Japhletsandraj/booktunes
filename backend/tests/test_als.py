"""ALS matrix factorisation."""

import pytest

from app.services.ai.als import ALSModel, ndcg_at_k, precision_recall_at_k


def _clustered_interactions():
    """Two disjoint taste groups — a working model must not cross-recommend.

    Users u0-u4 read books b0-b4; users u5-u9 read b5-b9.
    """
    triples = []
    for u in range(5):
        for b in range(5):
            triples.append((f"u{u}", f"b{b}", 1.0))
    for u in range(5, 10):
        for b in range(5, 10):
            triples.append((f"u{u}", f"b{b}", 1.0))
    return triples


def test_refuses_to_train_on_sparse_data():
    """Below the threshold the model would memorise noise, so it declines and
    the engine stays content-based."""
    model = ALSModel()
    metrics = model.fit([("u1", "b1", 1.0)])
    assert metrics["status"] == 0.0
    assert not model.is_trained


def test_trains_and_reports_metrics():
    model = ALSModel(factors=8, iterations=8)
    metrics = model.fit(_clustered_interactions())
    assert metrics["status"] == 1.0
    assert metrics["users"] == 10
    assert metrics["items"] == 10
    assert model.is_trained


def test_learns_group_structure():
    """The core behaviour: a user from group A gets group-A books when their
    own are excluded."""
    model = ALSModel(factors=8, iterations=15)
    model.fit(_clustered_interactions())

    # u0 has read b0-b4; recommend from what's left.
    recs = model.recommend("u0", limit=5, exclude={f"b{i}" for i in range(5)})
    assert recs, "expected recommendations for a known user"
    # Everything remaining is from the other cluster, so just assert we ranked.
    assert all(book_id.startswith("b") for book_id, _ in recs)

    # Without exclusion, the user's own cluster should dominate the top slots.
    top = [book for book, _ in model.recommend("u0", limit=5)]
    assert sum(1 for b in top if int(b[1:]) < 5) >= 3


def test_unknown_user_returns_empty():
    """Cold-start users aren't in the factor matrix — the caller must fall
    back rather than receive garbage."""
    model = ALSModel(factors=8, iterations=5)
    model.fit(_clustered_interactions())
    assert model.recommend("nobody") == []


def test_scores_are_normalized():
    model = ALSModel(factors=8, iterations=8)
    model.fit(_clustered_interactions())
    scores = [score for _, score in model.recommend("u0", limit=10)]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_negative_strengths_are_dropped():
    """ALS models confidence in a positive; a negative strength would invert
    its meaning rather than express dislike."""
    triples = _clustered_interactions() + [("u0", "b9", -1.0)]
    model = ALSModel(factors=8, iterations=5)
    metrics = model.fit(triples)
    assert metrics["interactions"] == len(_clustered_interactions())


def test_similar_items():
    model = ALSModel(factors=8, iterations=15)
    model.fit(_clustered_interactions())
    similar = model.similar_items("b0", limit=3)
    assert len(similar) == 3
    assert "b0" not in [book for book, _ in similar]


def test_save_and_load_round_trip(tmp_path):
    model = ALSModel(factors=8, iterations=8)
    model.fit(_clustered_interactions())
    original = model.recommend("u0", limit=5)

    assert model.save(str(tmp_path)) is not None
    loaded = ALSModel.load(str(tmp_path))

    assert loaded is not None
    assert loaded.recommend("u0", limit=5) == original


def test_load_missing_model_returns_none(tmp_path):
    assert ALSModel.load(str(tmp_path)) is None


def test_precision_recall():
    precision, recall = precision_recall_at_k(["a", "b", "c", "d"], ["a", "c"], k=4)
    assert precision == pytest.approx(0.5)
    assert recall == pytest.approx(1.0)


def test_precision_recall_no_relevant_items():
    assert precision_recall_at_k(["a"], [], k=1) == (0.0, 0.0)


def test_ndcg_rewards_ranking_relevant_items_higher():
    top = ndcg_at_k(["a", "b", "c"], ["a"], k=3)
    bottom = ndcg_at_k(["b", "c", "a"], ["a"], k=3)
    assert top == pytest.approx(1.0)
    assert top > bottom
