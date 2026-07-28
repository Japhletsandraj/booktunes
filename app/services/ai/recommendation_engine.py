"""Hybrid book recommendation engine.

Blends two signals:

* **Content-based** — cosine similarity between the user's preference vector
  and book embeddings, executed in-database via pgvector so we never pull the
  whole catalogue into memory.
* **Collaborative** — an ALS factorisation over implicit feedback, which finds
  "people like you also read X" patterns that content similarity can't see.

The blend is *adaptive*: collaborative filtering is worthless until there's
enough interaction data, so its weight scales with how much the model actually
knows about this user. A cold-start user gets ~100% content-based, and the mix
shifts toward collaborative as evidence accumulates.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set
from app.models import (
    Book,
    BookPlaylist,
    Recommendation,
    RecommendationFeedback,
    User,
    UserBookInteraction,
)
from app.services.ai import embeddings
from app.services.ai.als import ALSModel
from app.services.ai.preference_learner import UserPreferenceLearner

logger = get_logger(__name__)

RECS_CACHE_PREFIX = "recs:v1:"
SUMMARY_CACHE_PREFIX = "summary:v1:"
SUMMARY_TTL = 60 * 60 * 24

# The seven factors behind a match score. Weights sum to 1.0.
FACTOR_WEIGHTS: dict[str, float] = {
    "similarity": 0.20,       # resemblance to books they enjoyed
    "genre_match": 0.20,
    "author_affinity": 0.15,
    "mood_alignment": 0.15,
    "reading_level": 0.10,
    "playlist_synergy": 0.10,
    "time_commitment": 0.10,
}


class BookRecommendationEngine:
    """Stateless per-request wrapper around a process-wide ALS model."""

    # Shared across requests — training is expensive and the model is read-only
    # at inference time.
    _als_model: ALSModel | None = None
    _als_loaded = False

    def __init__(self, session: AsyncSession):
        self.session = session
        self.preferences = UserPreferenceLearner(session)

    # -- Model lifecycle --------------------------------------------------

    @classmethod
    def initialize_models(cls) -> None:
        """Load the persisted ALS model. Called once from app startup.

        Absence is normal and non-fatal — before the first nightly training
        run there is no model, and the engine degrades to content-based.
        """
        if cls._als_loaded:
            return
        cls._als_model = ALSModel.load(settings.MODEL_STORAGE_DIR)
        cls._als_loaded = True
        if cls._als_model is None:
            logger.info("No ALS model on disk — content-based only until first training")
        else:
            logger.info("ALS model ready: %s", cls._als_model.metrics)

    @classmethod
    def set_model(cls, model: ALSModel | None) -> None:
        """Swap in a freshly trained model without a restart."""
        cls._als_model = model
        cls._als_loaded = True

    @property
    def als(self) -> ALSModel | None:
        if not self._als_loaded:
            self.initialize_models()
        return self._als_model

    # -- Candidate generation ---------------------------------------------

    async def _seen_book_ids(self, user_id: uuid.UUID) -> set[str]:
        """Books to never recommend: already in the library, or explicitly
        rejected."""
        interacted = await self.session.scalars(
            select(UserBookInteraction.book_id).where(
                UserBookInteraction.user_id == user_id
            )
        )
        rejected = await self.session.scalars(
            select(RecommendationFeedback.book_id).where(
                RecommendationFeedback.user_id == user_id,
                RecommendationFeedback.feedback_type.in_(
                    ["dislike", "not_interested", "already_read"]
                ),
            )
        )
        return {str(b) for b in interacted.all()} | {str(b) for b in rejected.all()}

    async def content_based_filtering(
        self,
        user_embedding: Sequence[float],
        limit: int = 50,
        exclude: set[str] | None = None,
        genres: Sequence[str] | None = None,
    ) -> list[tuple[Book, float]]:
        """Nearest books by cosine distance, computed inside Postgres.

        pgvector's `<=>` returns cosine *distance* in [0, 2]; similarity is
        1 - distance. Ordering by the operator lets the ivfflat index serve
        the query instead of a full scan.
        """
        distance = Book.embedding.cosine_distance(list(user_embedding))
        query = (
            select(Book, distance.label("distance"))
            .where(Book.embedding.is_not(None))
            .order_by(distance)
            # Over-fetch: excluded books are filtered after ranking, and we
            # still need `limit` survivors.
            .limit(limit + len(exclude or ()) + 20)
        )
        if genres:
            query = query.where(Book.genres.overlap(list(genres)))

        rows = (await self.session.execute(query)).all()
        exclude = exclude or set()

        out: list[tuple[Book, float]] = []
        for book, dist in rows:
            if str(book.id) in exclude:
                continue
            out.append((book, max(0.0, 1.0 - float(dist))))
            if len(out) >= limit:
                break
        return out

    async def collaborative_filtering(
        self, user_id: uuid.UUID, limit: int = 50, exclude: set[str] | None = None
    ) -> list[tuple[Book, float]]:
        """ALS predictions, hydrated into Book rows."""
        model = self.als
        if model is None or not model.is_trained:
            return []

        ranked = model.recommend(str(user_id), limit=limit, exclude=exclude or set())
        if not ranked:
            return []

        score_by_id = dict(ranked)
        books = (
            await self.session.scalars(
                select(Book).where(Book.id.in_([uuid.UUID(b) for b in score_by_id]))
            )
        ).all()
        pairs = [(book, score_by_id[str(book.id)]) for book in books]
        pairs.sort(key=lambda pair: pair[1], reverse=True)
        return pairs

    async def _popular_books(
        self, limit: int = 50, exclude: set[str] | None = None
    ) -> list[tuple[Book, float]]:
        """Last-resort fallback for users with no signal whatsoever.

        Ranks by a Bayesian-damped rating so a single 5-star book with one
        vote doesn't outrank a 4.5-star book with a thousand.
        """
        prior_votes, prior_mean = 50, 3.5
        damped = (
            (
                func.coalesce(Book.average_rating, prior_mean)
                * func.coalesce(Book.rating_count, 0)
                + prior_mean * prior_votes
            )
            / (func.coalesce(Book.rating_count, 0) + prior_votes)
        )
        rows = (
            await self.session.execute(
                select(Book, damped.label("score"))
                .where(Book.embedding.is_not(None))
                .order_by(desc("score"))
                .limit(limit + len(exclude or ()))
            )
        ).all()

        exclude = exclude or set()
        out: list[tuple[Book, float]] = []
        for book, score in rows:
            if str(book.id) in exclude:
                continue
            out.append((book, min(1.0, float(score) / 5.0)))
            if len(out) >= limit:
                break
        return out

    # -- Blending ---------------------------------------------------------

    @staticmethod
    def _blend_weights(confidence: float, has_als: bool) -> dict[str, float]:
        """Decide the CF/CB mix from how much we know about the user.

        Collaborative filtering is only trustworthy once the user appears in
        the trained model with real history, so its weight is capped at 0.5
        even for heavy users — content similarity stays the anchor.
        """
        if not has_als:
            return {"collaborative": 0.0, "content": 1.0}
        collaborative = min(0.5, confidence * 0.6)
        return {
            "collaborative": round(collaborative, 3),
            "content": round(1.0 - collaborative, 3),
        }

    @staticmethod
    def _apply_diversity(
        scored: list[tuple[Book, float, dict[str, float]]],
        limit: int,
        max_per_author: int = 2,
        max_per_genre_ratio: float = 0.4,
    ) -> list[tuple[Book, float, dict[str, float]]]:
        """Greedy re-rank that caps author and genre repetition.

        Pure relevance ranking collapses into "ten books by one author" —
        technically optimal, useless as a shelf. Anything skipped is kept in
        reserve and used to backfill if the caps starve the list.
        """
        max_per_genre = max(2, int(limit * max_per_genre_ratio))
        author_counts: dict[str, int] = {}
        genre_counts: dict[str, int] = {}

        chosen: list[tuple[Book, float, dict[str, float]]] = []
        deferred: list[tuple[Book, float, dict[str, float]]] = []

        for book, score, factors in scored:
            author = (book.author or "").lower()
            primary_genre = (book.genres or ["unknown"])[0]

            if (
                author_counts.get(author, 0) >= max_per_author
                or genre_counts.get(primary_genre, 0) >= max_per_genre
            ):
                deferred.append((book, score, factors))
                continue

            chosen.append((book, score, factors))
            author_counts[author] = author_counts.get(author, 0) + 1
            genre_counts[primary_genre] = genre_counts.get(primary_genre, 0) + 1
            if len(chosen) >= limit:
                return chosen

        chosen.extend(deferred[: limit - len(chosen)])
        return chosen[:limit]

    # -- Factor scoring ---------------------------------------------------

    async def _user_context(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Everything the factor scorers need, fetched once per request."""
        user = await self.session.get(User, user_id)

        rows = (
            await self.session.execute(
                select(Book.genres, Book.author, Book.page_count, Book.moods,
                       UserBookInteraction.rating, UserBookInteraction.status)
                .join(UserBookInteraction, UserBookInteraction.book_id == Book.id)
                .where(UserBookInteraction.user_id == user_id)
                .limit(200)
            )
        ).all()

        genre_counts: dict[str, int] = {}
        author_counts: dict[str, int] = {}
        mood_totals: dict[str, float] = {}
        page_counts: list[int] = []

        for genres, author, pages, moods, rating, status in rows:
            liked = (rating is None or float(rating) >= 3.5) and status != "abandoned"
            if not liked:
                continue
            for genre in genres or []:
                genre_counts[genre] = genre_counts.get(genre, 0) + 1
            if author:
                author_counts[author.lower()] = author_counts.get(author.lower(), 0) + 1
            for mood, value in (moods or {}).items():
                mood_totals[mood] = mood_totals.get(mood, 0.0) + float(value)
            if pages:
                page_counts.append(int(pages))

        quiz = (user.preferences if user else {}) or {}
        # Quiz genres count as evidence too, so a new user's stated taste
        # actually shows up in the genre factor.
        for genre in quiz.get("favorite_genres", []) or []:
            genre_counts[genre] = genre_counts.get(genre, 0) + 2
        for author in quiz.get("favorite_authors", []) or []:
            author_counts[author.lower()] = author_counts.get(author.lower(), 0) + 2
        for mood in quiz.get("preferred_moods", []) or []:
            mood_totals[mood] = mood_totals.get(mood, 0.0) + 1.0

        avg_pages = sum(page_counts) / len(page_counts) if page_counts else None
        return {
            "user": user,
            "genre_counts": genre_counts,
            "author_counts": author_counts,
            "mood_totals": mood_totals,
            "avg_pages": avg_pages,
            "reading_level": (user.reading_level if user else None)
            or quiz.get("reading_level"),
            "preferred_length": quiz.get("preferred_length", "any"),
            "music_genres": quiz.get("music_genres", []) or [],
        }

    def _score_factors(
        self,
        book: Book,
        similarity: float,
        context: dict[str, Any],
        has_playlist: bool,
    ) -> dict[str, float]:
        """Score all seven factors on 0-100."""
        factors: dict[str, float] = {}

        # 1. Similarity to enjoyed books — cosine sim, rescaled. Real-world
        # sims cluster in 0.2-0.7, so map that band across the full range
        # instead of reporting everything as "40% match".
        factors["similarity"] = round(
            max(0.0, min(100.0, (similarity - 0.15) / 0.55 * 100)), 2
        )

        # 2. Genre match — share of the user's liked-genre mass this book hits.
        genre_counts: dict[str, int] = context["genre_counts"]
        total_genre = sum(genre_counts.values())
        if total_genre and book.genres:
            overlap = sum(genre_counts.get(g, 0) for g in book.genres)
            # sqrt keeps a single strong overlap from saturating instantly.
            factors["genre_match"] = round(
                min(100.0, (overlap / total_genre) ** 0.5 * 140), 2
            )
        else:
            factors["genre_match"] = 40.0  # neutral when we know nothing

        # 3. Author affinity — binary-ish; either they know this author or not.
        author_counts: dict[str, int] = context["author_counts"]
        author = (book.author or "").lower()
        if author_counts:
            direct = author_counts.get(author, 0)
            if direct:
                factors["author_affinity"] = round(min(100.0, 60 + direct * 20), 2)
            else:
                # Partial credit for a shared name in a multi-author string.
                partial = any(
                    known in author or author in known for known in author_counts
                )
                factors["author_affinity"] = 55.0 if partial else 30.0
        else:
            factors["author_affinity"] = 40.0

        # 4. Mood alignment — cosine over the mood dictionaries.
        mood_totals: dict[str, float] = context["mood_totals"]
        book_moods = book.moods or {}
        if mood_totals and book_moods:
            shared = set(mood_totals) & set(book_moods)
            if shared:
                dot = sum(mood_totals[m] * float(book_moods[m]) for m in shared)
                norm_u = sum(v * v for v in mood_totals.values()) ** 0.5
                norm_b = sum(float(v) ** 2 for v in book_moods.values()) ** 0.5
                factors["mood_alignment"] = round(
                    min(100.0, (dot / (norm_u * norm_b or 1e-9)) * 100), 2
                )
            else:
                factors["mood_alignment"] = 25.0
        else:
            factors["mood_alignment"] = 40.0

        # 5. Reading level — exact match ideal, one step away tolerable.
        order = {"beginner": 0, "intermediate": 1, "advanced": 2}
        user_level, book_level = context["reading_level"], book.reading_level
        if user_level and book_level and user_level in order and book_level in order:
            gap = abs(order[user_level] - order[book_level])
            factors["reading_level"] = {0: 100.0, 1: 65.0, 2: 30.0}[gap]
        else:
            factors["reading_level"] = 50.0

        # 6. Playlist synergy — a book with a playlist is the whole product
        # premise, so having one is worth real points.
        if has_playlist:
            music_overlap = bool(
                set(context["music_genres"]) & set(book.genres or [])
            )
            factors["playlist_synergy"] = 90.0 if music_overlap else 75.0
        else:
            factors["playlist_synergy"] = 35.0

        # 7. Time commitment — closeness to the length they actually finish.
        pages = book.page_count
        preferred = context["preferred_length"]
        target = context["avg_pages"]
        if target is None:
            target = {"short": 200, "medium": 350, "long": 600}.get(preferred or "any")
        if pages and target:
            ratio = abs(pages - target) / target
            factors["time_commitment"] = round(max(20.0, 100.0 - ratio * 70), 2)
        elif pages is None:
            factors["time_commitment"] = 50.0  # unknown length, stay neutral
        else:
            factors["time_commitment"] = 60.0

        return factors

    @staticmethod
    def _weighted_score(factors: dict[str, float]) -> float:
        return round(
            sum(factors.get(name, 50.0) * weight for name, weight in FACTOR_WEIGHTS.items()),
            2,
        )

    # -- Public API -------------------------------------------------------

    async def get_personalized_recommendations(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
        use_cache: bool = True,
        genres: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        cache_key = f"{RECS_CACHE_PREFIX}{user_id}:{limit}:{','.join(genres or [])}"
        if use_cache:
            if cached := await cache_get(cache_key):
                cached["cached"] = True
                return cached

        exclude = await self._seen_book_ids(user_id)
        user_embedding = await self.preferences.get_user_embedding(user_id)
        confidence = await self.preferences.get_confidence(user_id)

        has_als = bool(
            self.als and self.als.is_trained
            and str(user_id) in (self.als.user_index or {})
        )
        weights = self._blend_weights(confidence, has_als)

        # Over-fetch candidates so diversity filtering has room to work.
        candidate_n = max(limit * 4, 60)

        content_pairs: list[tuple[Book, float]] = []
        if user_embedding:
            content_pairs = await self.content_based_filtering(
                user_embedding, limit=candidate_n, exclude=exclude, genres=genres
            )

        collab_pairs: list[tuple[Book, float]] = []
        if weights["collaborative"] > 0:
            collab_pairs = await self.collaborative_filtering(
                user_id, limit=candidate_n, exclude=exclude
            )

        if not content_pairs and not collab_pairs:
            logger.info("No personalised signal for %s — falling back to popularity", user_id)
            content_pairs = await self._popular_books(limit=candidate_n, exclude=exclude)
            weights = {"collaborative": 0.0, "content": 0.0, "popularity": 1.0}

        # Merge the two candidate sets on book id, keeping each source's score.
        merged: dict[uuid.UUID, dict[str, Any]] = {}
        for book, score in content_pairs:
            merged[book.id] = {"book": book, "content": score, "collaborative": 0.0}
        for book, score in collab_pairs:
            entry = merged.setdefault(
                book.id, {"book": book, "content": 0.0, "collaborative": 0.0}
            )
            entry["collaborative"] = score

        if not merged:
            return {
                "items": [], "generated_at": datetime.now(UTC),
                "cached": False, "strategy": weights,
            }

        context = await self._user_context(user_id)
        playlist_book_ids = await self._books_with_playlists(list(merged.keys()))

        scored: list[tuple[Book, float, dict[str, float]]] = []
        for entry in merged.values():
            book = entry["book"]
            # Blend the two similarity signals before factor scoring so the
            # `similarity` factor reflects both sources.
            blended_similarity = (
                entry["content"] * weights.get("content", 0.0)
                + entry["collaborative"] * weights.get("collaborative", 0.0)
            )
            if weights.get("popularity"):
                blended_similarity = entry["content"]

            factors = self._score_factors(
                book,
                blended_similarity if blended_similarity > 0 else entry["content"],
                context,
                has_playlist=book.id in playlist_book_ids,
            )
            scored.append((book, self._weighted_score(factors), factors))

        scored.sort(key=lambda triple: triple[1], reverse=True)
        final = self._apply_diversity(scored, limit)

        confidence_pct = round(confidence * 100, 2)
        items = [
            {
                "book_id": str(book.id),
                "match_score": score,
                "confidence_score": confidence_pct,
                "factors": factors,
                "playlist_available": book.id in playlist_book_ids,
            }
            for book, score, factors in final
        ]

        payload = {
            "items": items,
            "generated_at": datetime.now(UTC).isoformat(),
            "cached": False,
            "strategy": weights,
        }

        await self._persist(user_id, final, confidence_pct)
        await cache_set(cache_key, payload, ttl=settings.RECOMMENDATION_CACHE_TTL)
        return payload

    async def _books_with_playlists(self, book_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if not book_ids:
            return set()
        rows = await self.session.scalars(
            select(BookPlaylist.book_id).where(BookPlaylist.book_id.in_(list(book_ids)))
        )
        return set(rows.all())

    async def _persist(
        self,
        user_id: uuid.UUID,
        scored: Sequence[tuple[Book, float, dict[str, float]]],
        confidence: float,
    ) -> None:
        """Upsert into `recommendations` so the batch job and API agree."""
        if not scored:
            return
        now = datetime.now(UTC)
        expires = now + timedelta(hours=24)
        try:
            for book, score, factors in scored:
                await self.session.execute(
                    pg_insert(Recommendation)
                    .values(
                        user_id=user_id, book_id=book.id, match_score=score,
                        confidence_score=confidence, factor_contributions=factors,
                        generated_at=now, expires_at=expires,
                    )
                    .on_conflict_do_update(
                        constraint="uq_recommendation_user_book",
                        set_={
                            "match_score": score,
                            "confidence_score": confidence,
                            "factor_contributions": factors,
                            "generated_at": now,
                            "expires_at": expires,
                        },
                    )
                )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            # Persistence is a cache warm, not the response — never fail the
            # request over it.
            logger.warning("Could not persist recommendations for %s: %s", user_id, exc)

    async def music_aware_ranking(
        self, book_ids: Sequence[uuid.UUID], user_id: uuid.UUID
    ) -> dict[uuid.UUID, float]:
        """Score how well each book's playlist matches the user's music taste."""
        if not book_ids:
            return {}
        user = await self.session.get(User, user_id)
        user_music = set((user.preferences or {}).get("music_genres", []) if user else [])

        rows = (
            await self.session.execute(
                select(BookPlaylist.book_id, BookPlaylist.mood_match_score,
                       BookPlaylist.genre_match_score)
                .where(BookPlaylist.book_id.in_(list(book_ids)))
            )
        ).all()

        out: dict[uuid.UUID, float] = {}
        for book_id, mood_score, genre_score in rows:
            base = float(mood_score or 50) * 0.5 + float(genre_score or 50) * 0.5
            # Without stated music taste we can't personalise, so return the
            # playlist's own intrinsic quality score.
            out[book_id] = round(min(100.0, base + (10 if user_music else 0)), 2)
        return out

    # -- Explanation ------------------------------------------------------

    async def generate_personalized_summary(
        self, user_id: uuid.UUID, book_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Explain in plain language why this book suits this user."""
        cache_key = f"{SUMMARY_CACHE_PREFIX}{user_id}:{book_id}"
        if cached := await cache_get(cache_key):
            return cached

        book = await self.session.get(Book, book_id)
        if book is None:
            return None

        user_embedding = await self.preferences.get_user_embedding(user_id)
        similarity = (
            embeddings.cosine_similarity(user_embedding, list(book.embedding))
            if user_embedding and book.embedding is not None
            else 0.0
        )

        context = await self._user_context(user_id)
        has_playlist = bool(await self._books_with_playlists([book_id]))
        factors = self._score_factors(book, similarity, context, has_playlist)
        match_score = self._weighted_score(factors)
        confidence = await self.preferences.get_confidence(user_id)

        detailed = [
            {
                "name": name,
                "score": factors[name],
                "weight": weight,
                "contribution": round(factors[name] * weight, 2),
                "explanation": self._explain(name, factors[name], book, context),
            }
            for name, weight in FACTOR_WEIGHTS.items()
        ]
        detailed.sort(key=lambda f: f["contribution"], reverse=True)

        payload = {
            "book_id": str(book_id),
            "user_id": str(user_id),
            "summary": self._compose_summary(book, detailed, match_score, confidence),
            "match_score": match_score,
            "confidence_score": round(confidence * 100, 2),
            "factors": detailed,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        await cache_set(cache_key, payload, ttl=SUMMARY_TTL)
        return payload

    @staticmethod
    def _explain(
        name: str, score: float, book: Book, context: dict[str, Any]
    ) -> str:
        """One sentence per factor, phrased by score band."""
        strong, weak = score >= 70, score < 40
        genres = ", ".join((book.genres or [])[:2]) or "this genre"

        if name == "genre_match":
            if strong:
                return f"{genres.capitalize()} is squarely in what you already read."
            return (
                f"{genres.capitalize()} is a step outside your usual shelf."
                if weak
                else f"{genres.capitalize()} partly overlaps your usual reading."
            )
        if name == "author_affinity":
            if strong:
                return f"You've read and enjoyed {book.author} before."
            return (
                f"{book.author} is new to you."
                if weak
                else f"{book.author} writes in a vein you've liked."
            )
        if name == "mood_alignment":
            moods = ", ".join(list((book.moods or {}).keys())[:2]) or "its tone"
            return (
                f"Its {moods} tone matches what you gravitate toward."
                if strong
                else f"Its {moods} tone is a change of pace for you."
            )
        if name == "reading_level":
            return (
                f"Pitched at your {book.reading_level or 'usual'} reading level."
                if strong
                else "The reading level sits outside your usual range."
            )
        if name == "playlist_synergy":
            return (
                "It comes with a matched Booktunes playlist."
                if score >= 70
                else "No playlist has been generated for it yet."
            )
        if name == "time_commitment":
            pages = book.page_count
            if not pages:
                return "Length is unknown for this edition."
            avg = context.get("avg_pages")
            if strong:
                return f"At {pages} pages it's the length you tend to finish."
            return f"At {pages} pages it's {'longer' if avg and pages > avg else 'shorter'} than your usual."
        if name == "similarity":
            return (
                "It closely resembles books you've rated highly."
                if strong
                else "It's thematically distant from your recent reads."
                if weak
                else "It shares some themes with your recent reads."
            )
        return ""

    @staticmethod
    def _compose_summary(
        book: Book,
        factors: list[dict[str, Any]],
        match_score: float,
        confidence: float,
    ) -> str:
        """Assemble the explanation from the top contributing factors.

        Template-based, not LLM-generated: it needs to be free, instant, and
        incapable of hallucinating a reason that isn't in the factor scores.
        """
        band = (
            "an excellent match" if match_score >= 80
            else "a strong match" if match_score >= 65
            else "a reasonable match" if match_score >= 50
            else "a bit of a stretch"
        )
        lines = [f"**{book.title}** by {book.author} looks like {band} for you ({match_score:.0f}/100)."]

        top = [f for f in factors if f["score"] >= 60][:3]
        if top:
            lines.append(" ".join(f["explanation"] for f in top))

        weakest = min(factors, key=lambda f: f["score"])
        if weakest["score"] < 40:
            # Naming the caveat is what makes the rest credible.
            lines.append(f"Worth knowing: {weakest['explanation'].lower()}")

        if confidence < 0.3:
            lines.append(
                "This is an early guess — rate a few more books and it'll sharpen up."
            )
        return " ".join(lines)

    # -- Training ---------------------------------------------------------

    async def collect_training_data(self) -> list[tuple[str, str, float]]:
        """Build implicit-feedback triples for ALS."""
        rows = (
            await self.session.execute(
                select(
                    UserBookInteraction.user_id,
                    UserBookInteraction.book_id,
                    UserBookInteraction.status,
                    UserBookInteraction.rating,
                )
            )
        ).all()

        strength_by_status = {
            "read": 1.0, "currently_reading": 0.6,
            "want_to_read": 0.3, "abandoned": 0.0,
        }
        triples: list[tuple[str, str, float]] = []
        for user_id, book_id, status, rating in rows:
            strength = strength_by_status.get(status or "", 0.2)
            if rating is not None:
                # Map a 0-5 rating onto 0-1 and take the stronger signal.
                strength = max(strength, max(0.0, (float(rating) - 2.0) / 3.0))
            if strength > 0:
                triples.append((str(user_id), str(book_id), strength))

        for user_id, book_id in (
            await self.session.execute(
                select(RecommendationFeedback.user_id, RecommendationFeedback.book_id)
                .where(RecommendationFeedback.feedback_type == "like")
            )
        ).all():
            triples.append((str(user_id), str(book_id), 0.7))

        return triples

    async def train_and_swap(self) -> dict[str, float]:
        """Retrain ALS and hot-swap it in if the fit succeeded."""
        triples = await self.collect_training_data()
        model = ALSModel()
        metrics = await asyncio.to_thread(model.fit, triples)

        if metrics.get("status") == 1.0:
            model.save(settings.MODEL_STORAGE_DIR)
            self.set_model(model)
            logger.info("ALS model retrained and swapped in")
        else:
            logger.info("Skipped ALS swap — insufficient training data")
        return metrics
