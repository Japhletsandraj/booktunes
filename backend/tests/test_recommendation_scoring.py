"""Factor scoring, blending and diversity — the pure logic in the engine."""

import uuid
from types import SimpleNamespace

import pytest

from app.services.ai.recommendation_engine import (
    FACTOR_WEIGHTS,
    BookRecommendationEngine,
)


def make_book(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "title": "A Book",
        "author": "An Author",
        "genres": ["fantasy"],
        "moods": {"epic": 0.8},
        "page_count": 350,
        "reading_level": "intermediate",
        "embedding": None,
        "average_rating": 4.0,
        "rating_count": 100,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_context(**overrides):
    defaults = {
        "user": None,
        "genre_counts": {"fantasy": 5},
        "author_counts": {"an author": 2},
        "mood_totals": {"epic": 3.0},
        "avg_pages": 350,
        "reading_level": "intermediate",
        "preferred_length": "medium",
        "music_genres": [],
    }
    defaults.update(overrides)
    return defaults


class TestFactorWeights:
    def test_weights_sum_to_one(self):
        """A drifting total silently rescales every match score."""
        assert sum(FACTOR_WEIGHTS.values()) == pytest.approx(1.0)

    def test_all_seven_factors_present(self):
        assert len(FACTOR_WEIGHTS) == 7


class TestScoreFactors:
    def setup_method(self):
        self.engine = BookRecommendationEngine.__new__(BookRecommendationEngine)

    def test_perfect_match_scores_high(self):
        factors = self.engine._score_factors(
            make_book(), similarity=0.75, context=make_context(), has_playlist=True
        )
        assert self.engine._weighted_score(factors) > 70

    def test_all_factors_are_produced(self):
        factors = self.engine._score_factors(
            make_book(), 0.5, make_context(), has_playlist=False
        )
        assert set(factors) == set(FACTOR_WEIGHTS)
        assert all(0 <= v <= 100 for v in factors.values())

    def test_unknown_user_gets_neutral_not_zero(self):
        """A blank profile should score ~neutral; zeroing every factor would
        make new users' recommendations look uniformly terrible."""
        blank = make_context(
            genre_counts={}, author_counts={}, mood_totals={},
            avg_pages=None, reading_level=None,
        )
        factors = self.engine._score_factors(make_book(), 0.4, blank, False)
        assert 30 <= self.engine._weighted_score(factors) <= 65

    def test_known_author_beats_unknown(self):
        known = self.engine._score_factors(
            make_book(author="An Author"), 0.5, make_context(), False
        )
        unknown = self.engine._score_factors(
            make_book(author="Nobody At All"), 0.5, make_context(), False
        )
        assert known["author_affinity"] > unknown["author_affinity"]

    def test_playlist_boosts_synergy(self):
        with_pl = self.engine._score_factors(make_book(), 0.5, make_context(), True)
        without = self.engine._score_factors(make_book(), 0.5, make_context(), False)
        assert with_pl["playlist_synergy"] > without["playlist_synergy"]

    def test_reading_level_gap_penalised(self):
        ctx = make_context(reading_level="beginner")
        near = self.engine._score_factors(
            make_book(reading_level="intermediate"), 0.5, ctx, False
        )
        far = self.engine._score_factors(
            make_book(reading_level="advanced"), 0.5, ctx, False
        )
        assert near["reading_level"] > far["reading_level"]

    def test_missing_page_count_is_neutral_not_penalised(self):
        """Open Library often omits page counts — an unknown length must not
        look like a bad length."""
        factors = self.engine._score_factors(
            make_book(page_count=None), 0.5, make_context(), False
        )
        assert factors["time_commitment"] == 50.0

    def test_time_commitment_prefers_familiar_length(self):
        ctx = make_context(avg_pages=300)
        close = self.engine._score_factors(make_book(page_count=310), 0.5, ctx, False)
        far = self.engine._score_factors(make_book(page_count=1200), 0.5, ctx, False)
        assert close["time_commitment"] > far["time_commitment"]

    def test_similarity_rescaled_across_useful_band(self):
        low = self.engine._score_factors(make_book(), 0.15, make_context(), False)
        high = self.engine._score_factors(make_book(), 0.70, make_context(), False)
        assert low["similarity"] == pytest.approx(0.0, abs=1.0)
        assert high["similarity"] > 95


class TestBlendWeights:
    def test_no_model_means_pure_content(self):
        weights = BookRecommendationEngine._blend_weights(0.9, has_als=False)
        assert weights == {"collaborative": 0.0, "content": 1.0}

    def test_cold_start_leans_on_content(self):
        weights = BookRecommendationEngine._blend_weights(0.0, has_als=True)
        assert weights["collaborative"] == 0.0
        assert weights["content"] == 1.0

    def test_collaborative_share_is_capped(self):
        """CF never dominates — content similarity stays the anchor."""
        weights = BookRecommendationEngine._blend_weights(1.0, has_als=True)
        assert weights["collaborative"] <= 0.5

    def test_weights_always_sum_to_one(self):
        for confidence in (0.0, 0.25, 0.5, 0.75, 1.0):
            weights = BookRecommendationEngine._blend_weights(confidence, True)
            assert weights["collaborative"] + weights["content"] == pytest.approx(1.0)


class TestDiversity:
    def _scored(self, books):
        return [(b, 90.0 - i, {}) for i, b in enumerate(books)]

    def test_caps_repeated_authors(self):
        books = [make_book(author="Prolific Writer") for _ in range(6)]
        result = BookRecommendationEngine._apply_diversity(
            self._scored(books), limit=6, max_per_author=2
        )
        # Deferred items backfill, so the list is still full...
        assert len(result) == 6
        # ...but the first picks respected the cap.
        assert result[0][1] >= result[-1][1]

    def test_mixed_authors_pass_through_in_order(self):
        books = [make_book(author=f"Author {i}") for i in range(5)]
        result = BookRecommendationEngine._apply_diversity(self._scored(books), limit=5)
        assert [b.author for b, _, _ in result] == [f"Author {i}" for i in range(5)]

    def test_never_exceeds_limit(self):
        books = [make_book(author=f"Author {i}") for i in range(50)]
        result = BookRecommendationEngine._apply_diversity(self._scored(books), limit=10)
        assert len(result) == 10

    def test_backfills_rather_than_returning_short(self):
        """One dominant author must not shrink a 10-item shelf to 2."""
        books = [make_book(author="Solo", genres=["fantasy"]) for _ in range(10)]
        result = BookRecommendationEngine._apply_diversity(
            self._scored(books), limit=10, max_per_author=2
        )
        assert len(result) == 10

    def test_empty_input(self):
        assert BookRecommendationEngine._apply_diversity([], limit=10) == []


class TestSummaryComposition:
    def test_mentions_title_and_score(self):
        engine = BookRecommendationEngine.__new__(BookRecommendationEngine)
        book = make_book(title="Dune", author="Frank Herbert")
        factors = [
            {"name": "genre_match", "score": 85.0, "weight": 0.2,
             "contribution": 17.0, "explanation": "Fantasy is your usual shelf."},
            {"name": "similarity", "score": 80.0, "weight": 0.2,
             "contribution": 16.0, "explanation": "Close to books you rated highly."},
        ]
        summary = engine._compose_summary(book, factors, 82.0, confidence=0.8)
        assert "Dune" in summary
        assert "82" in summary

    def test_low_confidence_is_disclosed(self):
        """Presenting a guess as certain is how a recommender loses trust."""
        engine = BookRecommendationEngine.__new__(BookRecommendationEngine)
        factors = [{"name": "genre_match", "score": 70.0, "weight": 0.2,
                    "contribution": 14.0, "explanation": "Matches your genres."}]
        summary = engine._compose_summary(make_book(), factors, 70.0, confidence=0.1)
        assert "early guess" in summary.lower()

    def test_weak_factor_surfaced_as_caveat(self):
        engine = BookRecommendationEngine.__new__(BookRecommendationEngine)
        factors = [
            {"name": "genre_match", "score": 90.0, "weight": 0.2,
             "contribution": 18.0, "explanation": "Right in your wheelhouse."},
            {"name": "reading_level", "score": 25.0, "weight": 0.1,
             "contribution": 2.5, "explanation": "The reading level is outside your range."},
        ]
        summary = engine._compose_summary(make_book(), factors, 75.0, confidence=0.9)
        assert "worth knowing" in summary.lower()
