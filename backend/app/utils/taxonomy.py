"""Genre normalisation and mood inference.

Open Library subjects are free-form and extremely noisy — a single book can
carry 40 subjects like "Fiction", "fiction", "New York Times bestseller",
"Accessible book", "Protected DAISY". This module collapses that into a small
controlled vocabulary the recommender can actually reason about.
"""

import re
from collections.abc import Iterable

# Canonical genres. Keep this list small — every genre is a dimension the
# recommender's genre-match factor has to have data for.
CANONICAL_GENRES = [
    "fiction", "nonfiction", "mystery", "thriller", "romance", "fantasy",
    "science_fiction", "horror", "historical_fiction", "literary_fiction",
    "young_adult", "children", "biography", "memoir", "history", "science",
    "philosophy", "poetry", "self_help", "business", "travel", "true_crime",
    "adventure", "classics", "graphic_novel", "short_stories", "dystopian",
]

# Substring -> canonical genre. Ordered longest-first at match time so
# "science fiction" wins over "science".
_GENRE_PATTERNS: dict[str, str] = {
    "science fiction": "science_fiction", "sci-fi": "science_fiction",
    "scifi": "science_fiction", "speculative fiction": "science_fiction",
    "historical fiction": "historical_fiction", "historical novel": "historical_fiction",
    "literary fiction": "literary_fiction",
    "young adult": "young_adult", "teen": "young_adult", "juvenile fiction": "young_adult",
    "children": "children", "juvenile literature": "children", "picture book": "children",
    "graphic novel": "graphic_novel", "comic": "graphic_novel", "manga": "graphic_novel",
    "short stories": "short_stories", "short story": "short_stories",
    "true crime": "true_crime",
    "self-help": "self_help", "self help": "self_help", "personal development": "self_help",
    "detective": "mystery", "mystery": "mystery", "crime": "mystery", "whodunit": "mystery",
    "thriller": "thriller", "suspense": "thriller", "espionage": "thriller",
    "romance": "romance", "love stor": "romance",
    "fantasy": "fantasy", "magic": "fantasy", "sword and sorcery": "fantasy",
    "horror": "horror", "ghost": "horror", "vampire": "horror", "supernatural": "horror",
    "dystopia": "dystopian", "post-apocalyptic": "dystopian",
    "biography": "biography", "autobiograph": "memoir", "memoir": "memoir",
    "history": "history", "historical": "history",
    "science": "science", "physics": "science", "biology": "science", "mathematics": "science",
    "philosophy": "philosophy", "ethics": "philosophy",
    "poetry": "poetry", "poems": "poetry",
    "business": "business", "economics": "business", "management": "business",
    "travel": "travel", "voyages": "travel",
    "adventure": "adventure",
    "classic": "classics",
    "nonfiction": "nonfiction", "non-fiction": "nonfiction",
    "fiction": "fiction",
}

# Subjects that carry no genre signal at all. Open Library is full of these.
_SUBJECT_NOISE = re.compile(
    r"(accessible book|protected daisy|in library|overdrive|large type|"
    r"bestseller|award|translations into|reading level|open library|"
    r"lending library|internet archive|nyt:|new york times)",
    re.IGNORECASE,
)

_PATTERNS_BY_LENGTH = sorted(_GENRE_PATTERNS.items(), key=lambda kv: -len(kv[0]))


def normalize_genres(raw_subjects: Iterable[str] | None, max_genres: int = 6) -> list[str]:
    """Map noisy subject strings onto the canonical vocabulary."""
    if not raw_subjects:
        return []

    seen: list[str] = []
    for subject in raw_subjects:
        if not subject or _SUBJECT_NOISE.search(subject):
            continue
        lowered = subject.lower().strip()
        for pattern, canonical in _PATTERNS_BY_LENGTH:
            if pattern in lowered:
                if canonical not in seen:
                    seen.append(canonical)
                break
        if len(seen) >= max_genres:
            break
    return seen[:max_genres]


# --- Moods ---------------------------------------------------------------

MOOD_VOCABULARY = [
    "dark", "uplifting", "melancholic", "tense", "whimsical", "romantic",
    "contemplative", "epic", "cozy", "mysterious", "humorous", "bittersweet",
]

# Keyword evidence for each mood, scanned over title + description + genres.
_MOOD_KEYWORDS: dict[str, list[str]] = {
    "dark": ["dark", "grim", "brutal", "murder", "death", "war", "violence", "tragedy"],
    "uplifting": ["hope", "inspiring", "triumph", "joy", "heartwarming", "redemption"],
    "melancholic": ["loss", "grief", "lonely", "sorrow", "nostalgia", "elegy"],
    "tense": ["thriller", "suspense", "race against", "danger", "chase", "conspiracy"],
    "whimsical": ["whimsical", "quirky", "fanciful", "playful", "absurd", "eccentric"],
    "romantic": ["love", "romance", "passion", "affair", "courtship", "wedding"],
    "contemplative": ["meditation", "philosoph", "reflect", "meaning", "consciousness"],
    "epic": ["epic", "saga", "kingdom", "empire", "quest", "destiny", "chronicle"],
    "cozy": ["cozy", "small town", "village", "tea", "bakery", "charming"],
    "mysterious": ["mystery", "secret", "hidden", "enigma", "puzzle", "clue"],
    "humorous": ["humor", "humour", "comic", "hilarious", "satire", "witty", "funny"],
    "bittersweet": ["bittersweet", "poignant", "coming of age", "farewell", "memory"],
}

# Genre -> moods that genre reliably implies, used when the description is
# missing or too short to score.
_GENRE_MOODS: dict[str, list[str]] = {
    "horror": ["dark", "tense", "mysterious"],
    "thriller": ["tense", "dark"],
    "mystery": ["mysterious", "tense"],
    "romance": ["romantic", "uplifting"],
    "fantasy": ["epic", "whimsical"],
    "science_fiction": ["contemplative", "epic"],
    "literary_fiction": ["contemplative", "melancholic"],
    "young_adult": ["bittersweet", "uplifting"],
    "children": ["whimsical", "uplifting"],
    "self_help": ["uplifting", "contemplative"],
    "philosophy": ["contemplative"],
    "poetry": ["contemplative", "melancholic"],
    "historical_fiction": ["epic", "bittersweet"],
    "dystopian": ["dark", "tense"],
    "adventure": ["epic"],
    "true_crime": ["dark", "tense"],
}


def infer_moods(
    title: str,
    description: str | None,
    genres: Iterable[str] | None,
) -> dict[str, float]:
    """Score each mood 0-1 from keyword evidence plus genre priors.

    This is deliberately a heuristic, not a classifier: a trained mood model
    would need labelled data we don't have, and the scores only ever feed a
    15%-weighted ranking factor.
    """
    haystack = " ".join(filter(None, [title or "", description or ""])).lower()
    genre_list = list(genres or [])

    scores: dict[str, float] = {}
    for mood in MOOD_VOCABULARY:
        hits = sum(1 for kw in _MOOD_KEYWORDS[mood] if kw in haystack)
        # Diminishing returns — three hits is strong, ten isn't 3x stronger.
        keyword_score = min(1.0, hits / 3.0)

        genre_score = 0.0
        for genre in genre_list:
            if mood in _GENRE_MOODS.get(genre, []):
                genre_score = max(genre_score, 0.6)

        combined = max(keyword_score, genre_score)
        if combined > 0:
            scores[mood] = round(combined, 3)

    return scores


def infer_reading_level(
    page_count: int | None, genres: Iterable[str] | None
) -> str:
    """Coarse reading-level guess from length and audience genre."""
    genre_set = set(genres or [])
    if "children" in genre_set:
        return "beginner"
    if "young_adult" in genre_set:
        return "intermediate"
    if genre_set & {"philosophy", "science", "literary_fiction", "classics"}:
        return "advanced"
    if page_count:
        if page_count < 200:
            return "beginner"
        if page_count < 450:
            return "intermediate"
        return "advanced"
    return "intermediate"
