"""Learns a user's taste vector from their interactions.

Two vectors are maintained per user because they answer different questions:

* **short_term** (30 days, fast decay) — "what are they into right now?"
* **long_term** (all time, slow decay) — "what do they reliably enjoy?"

Blending them stops a single genre binge from permanently rewriting someone's
profile, while still letting recommendations follow a genuine shift in taste.
"""

import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.core.redis_client import cache_delete, cache_get, cache_set
from app.models import (
    Book,
    ReadingProgress,
    RecommendationFeedback,
    User,
    UserBookInteraction,
    UserPreferenceEmbedding,
)
from app.services.ai import embeddings

logger = get_logger(__name__)

USER_EMBEDDING_CACHE_TTL = 60 * 60 * 6  # matches the 6-hourly batch job
_CACHE_PREFIX = "userpref:v1:"


class UserPreferenceLearner:
    # How much each signal says about taste. Negatives actively repel: a book
    # someone abandoned is evidence about what NOT to recommend, and dropping
    # that signal is why naive recommenders keep resurfacing rejected books.
    interaction_weights: dict[str, float] = {
        "finished_book": 1.0,
        "rated_4_5": 0.8,
        "read_50_percent": 0.7,
        "currently_reading": 0.6,
        "rated_3": 0.5,
        "added_to_library": 0.4,
        "saved_book": 0.3,
        "clicked": 0.1,
        "rated_1_2": -0.3,
        "abandoned": -0.5,
        "disliked": -0.6,
        "not_interested": -0.4,
    }

    window_size = 100          # most recent N interactions
    short_term_days = 30
    short_decay = 0.95         # per-week decay for the short-term vector
    long_decay = 0.99          # per-month decay for the long-term vector
    short_term_blend = 0.7     # baseline short/long mix

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- Signal extraction -----------------------------------------------

    def _classify(
        self,
        interaction: UserBookInteraction,
        progress_pct: float | None,
    ) -> tuple[str, float]:
        """Pick the single strongest signal this interaction represents."""
        rating = float(interaction.rating) if interaction.rating is not None else None

        if interaction.status == "abandoned":
            return "abandoned", self.interaction_weights["abandoned"]
        if rating is not None:
            if rating <= 2:
                return "rated_1_2", self.interaction_weights["rated_1_2"]
            if rating < 4:
                key = "rated_3"
            else:
                key = "rated_4_5"
            # A high rating on a finished book is the strongest positive we get.
            if interaction.status == "read" and key == "rated_4_5":
                return "finished_book", self.interaction_weights["finished_book"]
            return key, self.interaction_weights[key]
        if interaction.status == "read" or interaction.finished_reading_at:
            return "finished_book", self.interaction_weights["finished_book"]
        if interaction.status == "currently_reading":
            if progress_pct and progress_pct >= 50:
                return "read_50_percent", self.interaction_weights["read_50_percent"]
            return "currently_reading", self.interaction_weights["currently_reading"]
        if interaction.status == "want_to_read":
            return "added_to_library", self.interaction_weights["added_to_library"]
        return "clicked", self.interaction_weights["clicked"]

    @staticmethod
    def _time_decay(when: datetime | None, half_life_days: float) -> float:
        """Exponential decay on age. Missing timestamps get a neutral 0.5 so a
        NULL date neither boosts nor buries the signal."""
        if when is None:
            return 0.5
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - when).total_seconds() / 86400)
        return math.pow(0.5, age_days / half_life_days)

    async def _gather_signals(
        self, user_id: uuid.UUID, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Collect (embedding, weight, timestamp) triples for a user."""
        query = (
            select(UserBookInteraction, Book.embedding, ReadingProgress.percentage)
            .join(Book, Book.id == UserBookInteraction.book_id)
            .outerjoin(
                ReadingProgress,
                (ReadingProgress.book_id == UserBookInteraction.book_id)
                & (ReadingProgress.user_id == UserBookInteraction.user_id),
            )
            .where(
                UserBookInteraction.user_id == user_id,
                Book.embedding.is_not(None),
            )
            .order_by(UserBookInteraction.updated_at.desc())
            .limit(self.window_size)
        )
        if since is not None:
            query = query.where(UserBookInteraction.updated_at >= since)

        signals: list[dict[str, Any]] = []
        for interaction, embedding, pct in (await self.session.execute(query)).all():
            kind, weight = self._classify(interaction, float(pct) if pct else None)
            signals.append({
                "embedding": list(embedding),
                "base_weight": weight,
                "kind": kind,
                "at": interaction.last_interaction_at or interaction.updated_at,
            })

        # Explicit feedback is a strong, cheap signal — fold it in.
        feedback_query = (
            select(RecommendationFeedback, Book.embedding)
            .join(Book, Book.id == RecommendationFeedback.book_id)
            .where(
                RecommendationFeedback.user_id == user_id,
                RecommendationFeedback.feedback_type.in_(
                    ["like", "dislike", "not_interested"]
                ),
                Book.embedding.is_not(None),
            )
            .order_by(RecommendationFeedback.created_at.desc())
            .limit(50)
        )
        if since is not None:
            feedback_query = feedback_query.where(
                RecommendationFeedback.created_at >= since
            )

        for feedback, embedding in (await self.session.execute(feedback_query)).all():
            key = {
                "like": "rated_4_5",
                "dislike": "disliked",
                "not_interested": "not_interested",
            }[feedback.feedback_type]
            signals.append({
                "embedding": list(embedding),
                "base_weight": self.interaction_weights[key],
                "kind": key,
                "at": feedback.created_at,
            })

        return signals

    # -- Vector construction ---------------------------------------------

    def _build_vector(
        self, signals: Sequence[dict[str, Any]], half_life_days: float
    ) -> list[float] | None:
        if not signals:
            return None
        vectors = [s["embedding"] for s in signals]
        weights = [
            s["base_weight"] * self._time_decay(s["at"], half_life_days)
            for s in signals
        ]
        return embeddings.weighted_average(vectors, weights)

    async def calculate_short_term_preferences(
        self, user_id: uuid.UUID
    ) -> list[float] | None:
        since = datetime.now(UTC) - timedelta(days=self.short_term_days)
        signals = await self._gather_signals(user_id, since=since)
        # 7-day half-life: a two-week-old signal counts a quarter as much.
        return self._build_vector(signals, half_life_days=7.0)

    async def calculate_long_term_preferences(
        self, user_id: uuid.UUID
    ) -> list[float] | None:
        signals = await self._gather_signals(user_id)
        # 180-day half-life — taste erodes, but slowly.
        return self._build_vector(signals, half_life_days=180.0)

    def merge_preferences(
        self,
        short_term: list[float] | None,
        long_term: list[float] | None,
        activity_level: int = 0,
    ) -> list[float] | None:
        """Blend the two vectors, weighting short-term by how active the user is.

        A user with 2 recent interactions shouldn't have their whole profile
        driven by those 2 books, so the short-term share scales with evidence.
        """
        if short_term is None:
            return long_term
        if long_term is None:
            return short_term

        # 0 interactions -> 0.3 short; 20+ -> the full 0.7.
        ratio = min(1.0, activity_level / 20.0)
        short_weight = 0.3 + (self.short_term_blend - 0.3) * ratio
        return embeddings.weighted_average(
            [short_term, long_term], [short_weight, 1.0 - short_weight]
        )

    # -- Cold start -------------------------------------------------------

    async def embedding_from_quiz(self, preferences: dict[str, Any]) -> list[float] | None:
        """Turn preference-quiz answers into a starting vector.

        Without this a new user has no vector at all and falls back to pure
        popularity, which is the worst first impression a recommender can make.
        """
        genres = preferences.get("favorite_genres") or []
        authors = preferences.get("favorite_authors") or []
        moods = preferences.get("preferred_moods") or []
        if not (genres or authors or moods):
            return None

        parts = []
        if genres:
            parts.append("Genres: " + ", ".join(genres))
        if authors:
            parts.append("Books by " + ", ".join(authors))
        if moods:
            parts.append("Mood: " + ", ".join(moods))
        if level := preferences.get("reading_level"):
            parts.append(f"Reading level: {level}")

        return await embeddings.embed_text(". ".join(parts))

    # -- Persistence ------------------------------------------------------

    async def update_user_preferences(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Recompute and store both vectors. Run every 6 hours per user."""
        signals = await self._gather_signals(user_id)
        activity = len(signals)

        short_term = await self.calculate_short_term_preferences(user_id)
        long_term = await self.calculate_long_term_preferences(user_id)
        merged = self.merge_preferences(short_term, long_term, activity)

        if merged is None:
            # No usable interactions — fall back to the signup quiz.
            user = await self.session.get(User, user_id)
            if user and user.preferences:
                merged = await self.embedding_from_quiz(user.preferences)
                long_term = long_term or merged
            if merged is None:
                logger.debug("No preference signal for user %s", user_id)
                return {"updated": False, "reason": "no_signal", "signals": 0}

        weights = self._genre_weights(signals)

        await self.session.execute(
            pg_insert(UserPreferenceEmbedding)
            .values(
                user_id=user_id,
                embedding=merged,
                short_term_embedding=short_term,
                long_term_embedding=long_term,
                preference_weights=weights,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[UserPreferenceEmbedding.user_id],
                set_={
                    "embedding": merged,
                    "short_term_embedding": short_term,
                    "long_term_embedding": long_term,
                    "preference_weights": weights,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await self.session.commit()

        await cache_set(f"{_CACHE_PREFIX}{user_id}", merged, ttl=USER_EMBEDDING_CACHE_TTL)
        # The user's cached recommendations were built on the old vector.
        await cache_delete(f"recs:v1:{user_id}")

        logger.info("Updated preferences for %s from %d signals", user_id, activity)
        return {"updated": True, "signals": activity, "weights": weights}

    def _genre_weights(self, signals: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Summary stats stored alongside the vector for explainability."""
        positive = sum(1 for s in signals if s["base_weight"] > 0)
        negative = sum(1 for s in signals if s["base_weight"] < 0)
        return {
            "signal_count": len(signals),
            "positive_signals": positive,
            "negative_signals": negative,
            # Drives the confidence score surfaced to clients.
            "confidence": round(min(1.0, len(signals) / 20.0), 3),
        }

    async def get_user_embedding(
        self, user_id: uuid.UUID, use_cache: bool = True
    ) -> list[float] | None:
        """Cache-first read of a user's current vector."""
        key = f"{_CACHE_PREFIX}{user_id}"
        if use_cache:
            if cached := await cache_get(key):
                return cached

        row = await self.session.get(UserPreferenceEmbedding, user_id)
        if row and row.embedding is not None:
            vector = list(row.embedding)
            await cache_set(key, vector, ttl=USER_EMBEDDING_CACHE_TTL)
            return vector

        # Never computed — derive from the quiz on the fly rather than
        # returning nothing and forcing a popularity fallback.
        user = await self.session.get(User, user_id)
        if user and user.preferences:
            if vector := await self.embedding_from_quiz(user.preferences):
                await cache_set(key, vector, ttl=3600)
                return vector
        return None

    async def get_confidence(self, user_id: uuid.UUID) -> float:
        """0-1 measure of how much evidence backs this user's profile."""
        row = await self.session.get(UserPreferenceEmbedding, user_id)
        if row and row.preference_weights:
            return float(row.preference_weights.get("confidence", 0.0))
        user = await self.session.get(User, user_id)
        # A completed quiz is weak but non-zero evidence.
        return 0.2 if (user and user.preferences) else 0.0

    async def users_needing_update(self, hours: int = 6, limit: int = 500) -> list[uuid.UUID]:
        """Users whose interactions changed since their vector was last built."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        rows = await self.session.execute(
            select(UserBookInteraction.user_id)
            .outerjoin(
                UserPreferenceEmbedding,
                UserPreferenceEmbedding.user_id == UserBookInteraction.user_id,
            )
            .where(
                UserBookInteraction.updated_at >= cutoff,
                (UserPreferenceEmbedding.updated_at.is_(None))
                | (UserPreferenceEmbedding.updated_at < UserBookInteraction.updated_at),
            )
            .distinct()
            .limit(limit)
        )
        return [row[0] for row in rows.all()]
