"""Genre normalisation and mood inference."""

from app.utils import taxonomy


def test_longest_pattern_wins():
    """'science fiction' must not be captured by the 'science' rule."""
    assert taxonomy.normalize_genres(["Science Fiction"]) == ["science_fiction"]
    assert taxonomy.normalize_genres(["Popular science"]) == ["science"]


def test_noise_subjects_are_dropped():
    noisy = [
        "Accessible book", "Protected DAISY", "In library",
        "New York Times bestseller", "Fantasy fiction",
    ]
    assert taxonomy.normalize_genres(noisy) == ["fantasy"]


def test_genres_deduplicated_and_capped():
    subjects = ["Fantasy", "fantasy fiction", "Magic", "Mystery", "Detective"]
    result = taxonomy.normalize_genres(subjects, max_genres=3)
    assert result == list(dict.fromkeys(result))  # no repeats
    assert len(result) <= 3


def test_empty_input_returns_empty():
    assert taxonomy.normalize_genres(None) == []
    assert taxonomy.normalize_genres([]) == []


def test_moods_from_keywords():
    moods = taxonomy.infer_moods(
        "The Silent Grave",
        "A grim tale of murder and death in a war-torn city.",
        [],
    )
    assert moods.get("dark", 0) > 0.5


def test_moods_fall_back_to_genre_priors():
    """A book with no description still gets mood signal from its genre —
    otherwise the mood factor is blind for most Open Library records."""
    moods = taxonomy.infer_moods("Untitled", None, ["horror"])
    assert moods.get("dark", 0) >= 0.6
    assert moods.get("tense", 0) >= 0.6


def test_mood_scores_are_bounded():
    moods = taxonomy.infer_moods(
        "Dark dark dark",
        "dark grim brutal murder death war violence tragedy " * 10,
        ["horror", "thriller"],
    )
    assert all(0.0 <= v <= 1.0 for v in moods.values())


def test_reading_level_prefers_audience_over_length():
    # A 600-page children's book is still a beginner read.
    assert taxonomy.infer_reading_level(600, ["children"]) == "beginner"
    assert taxonomy.infer_reading_level(150, ["philosophy"]) == "advanced"


def test_reading_level_falls_back_to_page_count():
    assert taxonomy.infer_reading_level(120, []) == "beginner"
    assert taxonomy.infer_reading_level(300, []) == "intermediate"
    assert taxonomy.infer_reading_level(700, []) == "advanced"
    assert taxonomy.infer_reading_level(None, []) == "intermediate"
