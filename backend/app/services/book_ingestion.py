"""Book ingestion from Open Library (primary) and Google Books (fallback).

Open Library is the primary source because it has no API key, no quota, and
permissive licensing. Its weakness is descriptions — a large fraction of works
have none, and the recommender is far weaker without them. So Google Books is
used to backfill missing descriptions and page counts, not just as an outage
fallback.
"""

import asyncio
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging_config import get_logger
from app.models import Book
from app.services.ai import embeddings
from app.utils.http import fetch_json
from app.utils.rate_limiter import google_books_limiter, open_library_limiter
from app.utils.taxonomy import infer_moods, infer_reading_level, normalize_genres

logger = get_logger(__name__)

OPEN_LIBRARY_BASE = "https://openlibrary.org"
OPEN_LIBRARY_COVERS = "https://covers.openlibrary.org/b"
GOOGLE_BOOKS_BASE = "https://www.googleapis.com/books/v1"

# Genres to pull during the initial seed, sized to land near 1000 books while
# staying inside Supabase's 500MB.
SEED_GENRES: dict[str, int] = {
    "fiction": 120,
    "mystery": 80,
    "romance": 80,
    "fantasy": 90,
    "science_fiction": 90,
    "thriller": 70,
    "horror": 60,
    "historical_fiction": 70,
    "literary_fiction": 60,
    "young_adult": 70,
    "children": 50,
    "biography": 50,
    "poetry": 40,
    "philosophy": 40,
    "adventure": 40,
}


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    # Open Library descriptions often carry a trailing source credit like
    # "([source][1])" or "--back cover", which is noise in the embedding.
    text = re.sub(r"\(\[.*?\]\[\d+\]\)", "", value)
    text = re.sub(r"-{2,}\s*(back cover|from the publisher).*$", "", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _extract_description(payload: Any) -> str | None:
    """Open Library returns description as either a string or {type, value}."""
    if isinstance(payload, str):
        return _clean_text(payload)
    if isinstance(payload, dict):
        return _clean_text(payload.get("value"))
    return None


class BookIngestionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.batch_size = 10  # keeps peak memory low during embedding

    # -- Open Library ----------------------------------------------------

    async def fetch_books_by_genre(
        self, genre: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch works for a subject, paging 50 at a time."""
        subject = genre.replace("_", "_")  # OL subjects use underscores already
        collected: list[dict[str, Any]] = []
        offset = 0
        page_size = 50

        while len(collected) < limit:
            await open_library_limiter.acquire()
            try:
                payload = await fetch_json(
                    f"{OPEN_LIBRARY_BASE}/subjects/{subject}.json",
                    params={"limit": min(page_size, limit - len(collected)),
                            "offset": offset, "details": "false"},
                )
            except Exception as exc:
                logger.error("Open Library subject '%s' failed at offset %d: %s",
                             subject, offset, exc)
                break

            if not payload:
                break
            works = payload.get("works") or []
            if not works:
                break  # exhausted the subject

            for work in works:
                parsed = self._parse_open_library_work(work, genre)
                if parsed:
                    collected.append(parsed)

            offset += len(works)
            if len(works) < page_size:
                break

        logger.info("Open Library: %d books for genre '%s'", len(collected), genre)
        return collected[:limit]

    def _parse_open_library_work(
        self, work: dict[str, Any], fallback_genre: str
    ) -> dict[str, Any] | None:
        title = _clean_text(work.get("title"))
        if not title:
            return None

        authors = work.get("authors") or []
        author = _clean_text(
            ", ".join(a.get("name", "") for a in authors if a.get("name"))
        ) or "Unknown"

        cover_id = work.get("cover_id") or work.get("cover_i")
        cover_url = f"{OPEN_LIBRARY_COVERS}/id/{cover_id}-L.jpg" if cover_id else None

        subjects = work.get("subject") or []
        genres = normalize_genres(subjects) or [fallback_genre]

        work_key = work.get("key")  # e.g. "/works/OL12345W"
        return {
            "title": title[:500],
            "author": author[:300],
            "description": _extract_description(work.get("description")),
            "cover_url": cover_url,
            "isbn": None,
            "publication_year": work.get("first_publish_year"),
            "page_count": None,
            "genres": genres,
            "source_ids": {
                "open_library_id": work_key,
                "open_library_cover_id": cover_id,
            },
            "_raw_subjects": subjects[:20],
        }

    async def enrich_from_open_library_work(self, work_key: str) -> dict[str, Any]:
        """Fetch the work detail page for a description Open Library's subject
        listing omits."""
        if not work_key:
            return {}
        await open_library_limiter.acquire()
        try:
            payload = await fetch_json(f"{OPEN_LIBRARY_BASE}{work_key}.json")
        except Exception as exc:
            logger.debug("Work detail %s failed: %s", work_key, exc)
            return {}
        if not payload:
            return {}
        return {"description": _extract_description(payload.get("description"))}

    # -- Google Books ----------------------------------------------------

    async def fetch_book_by_isbn(self, isbn: str) -> dict[str, Any] | None:
        """Look a book up by ISBN via Google Books."""
        return await self._google_books_query(f"isbn:{isbn}")

    async def fetch_google_metadata(
        self, title: str, author: str
    ) -> dict[str, Any] | None:
        """Backfill description/page count that Open Library lacks."""
        query = f'intitle:"{title[:100]}"'
        if author and author != "Unknown":
            query += f' inauthor:"{author.split(",")[0][:60]}"'
        return await self._google_books_query(query)

    async def _google_books_query(self, query: str) -> dict[str, Any] | None:
        await google_books_limiter.acquire()
        params: dict[str, Any] = {"q": query, "maxResults": 1, "country": "US"}
        # The key is optional — unauthenticated calls get ~1000/day per IP,
        # which is enough for backfill but not for a full re-seed.
        if settings.GOOGLE_BOOKS_API_KEY:
            params["key"] = settings.GOOGLE_BOOKS_API_KEY

        try:
            payload = await fetch_json(f"{GOOGLE_BOOKS_BASE}/volumes", params=params)
        except Exception as exc:
            logger.debug("Google Books query %r failed: %s", query, exc)
            return None

        items = (payload or {}).get("items") or []
        if not items:
            return None
        return self._parse_google_volume(items[0])

    def _parse_google_volume(self, volume: dict[str, Any]) -> dict[str, Any] | None:
        info = volume.get("volumeInfo") or {}
        title = _clean_text(info.get("title"))
        if not title:
            return None

        isbn_13 = isbn_10 = None
        for ident in info.get("industryIdentifiers") or []:
            if ident.get("type") == "ISBN_13":
                isbn_13 = ident.get("identifier")
            elif ident.get("type") == "ISBN_10":
                isbn_10 = ident.get("identifier")

        published = info.get("publishedDate") or ""
        year = None
        if match := re.match(r"(\d{4})", published):
            year = int(match.group(1))

        images = info.get("imageLinks") or {}
        cover = images.get("thumbnail") or images.get("smallThumbnail")
        if cover:
            cover = cover.replace("http://", "https://")

        return {
            "title": title[:500],
            "author": (", ".join(info.get("authors") or []) or "Unknown")[:300],
            "description": _clean_text(info.get("description")),
            "cover_url": cover,
            "isbn": isbn_13 or isbn_10,
            "publication_year": year,
            "page_count": info.get("pageCount"),
            "genres": normalize_genres(info.get("categories") or []),
            "average_rating": info.get("averageRating"),
            "rating_count": info.get("ratingsCount") or 0,
            "source_ids": {
                "google_books_id": volume.get("id"),
                "isbn_13": isbn_13,
                "isbn_10": isbn_10,
            },
        }

    # -- Enrichment + embedding -----------------------------------------

    async def enrich(self, book: dict[str, Any]) -> dict[str, Any]:
        """Fill gaps from secondary sources, then derive moods/level."""
        if not book.get("description"):
            work_key = (book.get("source_ids") or {}).get("open_library_id")
            if work_key:
                detail = await self.enrich_from_open_library_work(work_key)
                if detail.get("description"):
                    book["description"] = detail["description"]

        if not book.get("description") or not book.get("page_count"):
            google = await self.fetch_google_metadata(book["title"], book["author"])
            if google:
                book["description"] = book.get("description") or google.get("description")
                book["page_count"] = book.get("page_count") or google.get("page_count")
                book["isbn"] = book.get("isbn") or google.get("isbn")
                book["publication_year"] = (
                    book.get("publication_year") or google.get("publication_year")
                )
                book["average_rating"] = (
                    book.get("average_rating") or google.get("average_rating")
                )
                book["rating_count"] = book.get("rating_count") or google.get("rating_count", 0)
                merged = {**(book.get("source_ids") or {}), **(google.get("source_ids") or {})}
                book["source_ids"] = merged
                if not book.get("genres") and google.get("genres"):
                    book["genres"] = google["genres"]

        book["moods"] = infer_moods(
            book["title"], book.get("description"), book.get("genres")
        )
        book["reading_level"] = infer_reading_level(
            book.get("page_count"), book.get("genres")
        )
        book.pop("_raw_subjects", None)
        return book

    async def generate_embedding(self, book: dict[str, Any]) -> list[float] | None:
        text = embeddings.build_book_text(
            title=book["title"],
            author=book["author"],
            description=book.get("description"),
            genres=book.get("genres"),
            moods=book.get("moods"),
        )
        return await embeddings.embed_text(text)

    # -- Persistence -----------------------------------------------------

    async def _find_existing(self, book: dict[str, Any]) -> Book | None:
        """Deduplicate on ISBN first, then Open Library work id, then a
        case-insensitive title+author match."""
        isbn = book.get("isbn")
        if isbn:
            found = await self.session.scalar(select(Book).where(Book.isbn == isbn))
            if found:
                return found

        work_key = (book.get("source_ids") or {}).get("open_library_id")
        if work_key:
            found = await self.session.scalar(
                select(Book).where(
                    Book.source_ids["open_library_id"].astext == work_key
                )
            )
            if found:
                return found

        return await self.session.scalar(
            select(Book).where(
                func.lower(Book.title) == book["title"].lower(),
                func.lower(Book.author) == book["author"].lower(),
            )
        )

    async def process_batch(self, books: Sequence[dict[str, Any]]) -> dict[str, int]:
        """Enrich, embed and upsert a batch. Returns per-outcome counts."""
        stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
        if not books:
            return stats

        enriched: list[dict[str, Any]] = []
        for book in books:
            try:
                enriched.append(await self.enrich(dict(book)))
            except Exception as exc:
                logger.warning("Enrichment failed for %r: %s", book.get("title"), exc)
                stats["failed"] += 1

        # One batched forward pass beats N single encodes by a wide margin.
        texts = [
            embeddings.build_book_text(
                b["title"], b["author"], b.get("description"),
                b.get("genres"), b.get("moods"),
            )
            for b in enriched
        ]
        vectors = await embeddings.embed_batch(texts)

        # strict=True: embed_batch returns one slot per input, so a mismatch
        # means a bug — better to raise than to store a book's neighbour's
        # embedding.
        for book, vector in zip(enriched, vectors, strict=True):
            try:
                existing = await self._find_existing(book)
                if existing:
                    # Only fill blanks — never overwrite curated data with a
                    # thinner record from a later crawl.
                    changed = False
                    for field in ("description", "cover_url", "isbn", "page_count",
                                  "publication_year", "reading_level"):
                        if getattr(existing, field) is None and book.get(field) is not None:
                            setattr(existing, field, book[field])
                            changed = True
                    if not existing.genres and book.get("genres"):
                        existing.genres = book["genres"]
                        changed = True
                    if existing.embedding is None and vector is not None:
                        existing.embedding = vector
                        changed = True
                    stats["updated" if changed else "skipped"] += 1
                    continue

                self.session.add(
                    Book(
                        title=book["title"],
                        author=book["author"],
                        description=book.get("description"),
                        cover_url=book.get("cover_url"),
                        isbn=book.get("isbn"),
                        publication_year=book.get("publication_year"),
                        page_count=book.get("page_count"),
                        genres=book.get("genres") or [],
                        moods=book.get("moods") or {},
                        reading_level=book.get("reading_level"),
                        average_rating=book.get("average_rating"),
                        rating_count=book.get("rating_count") or 0,
                        embedding=vector,
                        source_ids=book.get("source_ids") or {},
                    )
                )
                stats["inserted"] += 1
            except Exception as exc:
                logger.warning("Persist failed for %r: %s", book.get("title"), exc)
                stats["failed"] += 1

        try:
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            logger.error("Batch commit failed: %s", exc)
            stats["failed"] += stats["inserted"] + stats["updated"]
            stats["inserted"] = stats["updated"] = 0

        return stats

    async def ingest_genre(self, genre: str, limit: int) -> dict[str, int]:
        totals = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
        books = await self.fetch_books_by_genre(genre, limit)
        for start in range(0, len(books), self.batch_size):
            chunk = books[start : start + self.batch_size]
            result = await self.process_batch(chunk)
            for key, value in result.items():
                totals[key] += value
            logger.info(
                "genre=%s progress=%d/%d inserted=%d updated=%d",
                genre, min(start + self.batch_size, len(books)), len(books),
                totals["inserted"], totals["updated"],
            )
            # Breathe between batches — keeps us clear of Open Library's
            # abuse heuristics on a long seed run.
            await asyncio.sleep(0.5)
        return totals

    async def initial_seed(
        self, genres: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """Seed the catalogue. Safe to re-run — dedup makes it idempotent."""
        plan = genres or SEED_GENRES
        overall = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
        per_genre: dict[str, dict[str, int]] = {}

        logger.info("Starting seed across %d genres (~%d books)",
                    len(plan), sum(plan.values()))

        for genre, limit in plan.items():
            try:
                result = await self.ingest_genre(genre, limit)
            except Exception as exc:
                logger.error("Genre '%s' aborted: %s", genre, exc)
                continue
            per_genre[genre] = result
            for key, value in result.items():
                overall[key] += value

        logger.info("Seed complete: %s", overall)
        logger.warning(
            "Rebuild the ivfflat index now that books exist: "
            "python -m scripts.rebuild_vector_index"
        )
        return {"totals": overall, "per_genre": per_genre}

    async def backfill_embeddings(self, limit: int = 200) -> int:
        """Embed books that were stored without a vector."""
        rows = (
            await self.session.scalars(
                select(Book).where(Book.embedding.is_(None)).limit(limit)
            )
        ).all()
        if not rows:
            return 0

        texts = [
            embeddings.build_book_text(
                b.title, b.author, b.description, b.genres, b.moods
            )
            for b in rows
        ]
        vectors = await embeddings.embed_batch(texts)
        updated = 0
        for book, vector in zip(rows, vectors, strict=True):
            if vector is not None:
                book.embedding = vector
                updated += 1
        await self.session.commit()
        logger.info("Backfilled %d embeddings", updated)
        return updated


# --- Cloudinary ----------------------------------------------------------
#
# Covers are served straight from Open Library / Google by default, which costs
# nothing and stays well inside the free tier. Mirroring to Cloudinary is only
# worth it if you need transformations or want to stop hotlinking.
#
# To enable: set CLOUDINARY_URL, then call `mirror_cover_to_cloudinary` from
# `process_batch` after insert. Budget ~25KB/cover — 25GB is far more than
# 1000 books need, so this is not a quota risk.

async def mirror_cover_to_cloudinary(
    image_url: str, public_id: str
) -> str | None:
    """Upload a remote cover to Cloudinary and return the CDN URL."""
    if not settings.CLOUDINARY_URL:
        return None
    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL, secure=True)
        # The SDK is sync-only; keep it off the event loop.
        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            image_url,
            public_id=f"booktunes/covers/{public_id}",
            overwrite=False,
            resource_type="image",
            transformation=[{"width": 400, "crop": "limit", "quality": "auto"}],
        )
        return result.get("secure_url")
    except Exception as exc:
        logger.warning("Cloudinary upload failed for %s: %s", public_id, exc)
        return None
