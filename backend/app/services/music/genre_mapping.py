"""Book genre / mood -> music mappings.

Everything here is editorial judgement, not learned. It's the layer that makes
a fantasy novel return orchestral scores rather than whatever Spotify's search
happens to surface for the word "fantasy" — which is mostly unrelated pop.
"""


# Book genre -> Spotify seed genres. Only genres from Spotify's official seed
# list are usable with the recommendations endpoint; free-text search accepts
# anything, so a few entries here are search-only descriptors.
GENRE_TO_MUSIC: dict[str, list[str]] = {
    "fantasy": ["soundtracks", "classical", "folk"],
    "science_fiction": ["electronic", "ambient", "synth-pop"],
    "mystery": ["jazz", "trip-hop", "blues"],
    "thriller": ["electronic", "industrial", "drum-and-bass"],
    "horror": ["industrial", "dark-ambient", "metal"],
    "romance": ["pop", "r-n-b", "acoustic"],
    "historical_fiction": ["classical", "folk", "world-music"],
    "literary_fiction": ["indie", "singer-songwriter", "chill"],
    "young_adult": ["indie-pop", "pop", "alt-rock"],
    "children": ["happy", "acoustic", "disney"],
    "biography": ["singer-songwriter", "folk", "soul"],
    "memoir": ["soul", "singer-songwriter", "jazz"],
    "history": ["classical", "world-music", "folk"],
    "science": ["ambient", "minimal-techno", "electronic"],
    "philosophy": ["ambient", "classical", "post-rock"],
    "poetry": ["singer-songwriter", "ambient", "jazz"],
    "self_help": ["chill", "happy", "acoustic"],
    "business": ["electronic", "chill", "jazz"],
    "travel": ["world-music", "reggae", "bossanova"],
    "true_crime": ["trip-hop", "industrial", "blues"],
    "adventure": ["soundtracks", "rock", "folk"],
    "classics": ["classical", "opera", "jazz"],
    "graphic_novel": ["rock", "electronic", "punk"],
    "short_stories": ["indie", "jazz", "chill"],
    "dystopian": ["industrial", "ambient", "electronic"],
    "fiction": ["indie", "chill", "acoustic"],
    "nonfiction": ["chill", "ambient", "jazz"],
}

# Mood -> Spotify audio-feature targets.
#
# NOTE: Spotify deprecated the audio-features and recommendations endpoints for
# apps created after 2024-11-27. These targets are still applied when the
# endpoint is reachable (older apps), and otherwise inform the search-keyword
# path below. See music_service.MusicService.get_spotify_recommendations.
MOOD_TO_AUDIO_FEATURES: dict[str, dict[str, float]] = {
    "dark":          {"target_valence": 0.20, "target_energy": 0.45, "target_mode": 0},
    "uplifting":     {"target_valence": 0.85, "target_energy": 0.70},
    "melancholic":   {"target_valence": 0.20, "target_energy": 0.30, "target_acousticness": 0.7},
    "tense":         {"target_valence": 0.30, "target_energy": 0.85, "target_tempo": 140},
    "whimsical":     {"target_valence": 0.75, "target_energy": 0.55, "target_acousticness": 0.5},
    "romantic":      {"target_valence": 0.65, "target_energy": 0.40, "target_acousticness": 0.6},
    "contemplative": {"target_valence": 0.40, "target_energy": 0.25, "target_instrumentalness": 0.7},
    "epic":          {"target_valence": 0.50, "target_energy": 0.85, "target_instrumentalness": 0.6},
    "cozy":          {"target_valence": 0.70, "target_energy": 0.30, "target_acousticness": 0.8},
    "mysterious":    {"target_valence": 0.30, "target_energy": 0.40, "target_instrumentalness": 0.5},
    "humorous":      {"target_valence": 0.90, "target_energy": 0.65},
    "bittersweet":   {"target_valence": 0.40, "target_energy": 0.40, "target_acousticness": 0.6},
}

# Search keywords per mood — the fallback when audio-feature targeting isn't
# available. Deliberately phrased the way people name playlists.
MOOD_SEARCH_TERMS: dict[str, list[str]] = {
    "dark": ["dark ambient", "brooding", "sinister score"],
    "uplifting": ["feel good", "uplifting", "triumphant"],
    "melancholic": ["melancholy", "sad piano", "wistful"],
    "tense": ["tension", "suspense score", "pulsing"],
    "whimsical": ["whimsical", "playful strings", "quirky"],
    "romantic": ["romantic", "love songs", "tender"],
    "contemplative": ["contemplative", "meditative", "reflective piano"],
    "epic": ["epic orchestral", "cinematic", "heroic"],
    "cozy": ["cozy", "warm acoustic", "fireside"],
    "mysterious": ["mysterious", "noir jazz", "enigmatic"],
    "humorous": ["upbeat quirky", "cheerful", "lighthearted"],
    "bittersweet": ["bittersweet", "nostalgic", "poignant"],
}

# Book genre -> curation tags, used to search Deezer for playlists.
#
# These must be terms people actually name playlists after, not descriptive
# prose — an invented tag like "brooding sinister score" matches nothing.
# Where a mood word is a real genre name ("dark ambient", "melancholy") it is
# used; otherwise the nearest well-populated genre name stands in.
GENRE_TO_MUSIC_TAGS: dict[str, list[str]] = {
    "fantasy": ["soundtrack", "epic", "celtic"],
    "science_fiction": ["ambient", "electronic", "synthwave"],
    "mystery": ["trip-hop", "jazz", "downtempo"],
    "thriller": ["industrial", "drum and bass", "electronic"],
    "horror": ["dark ambient", "industrial", "doom metal"],
    "romance": ["soul", "rnb", "acoustic"],
    "historical_fiction": ["classical", "folk", "world"],
    "literary_fiction": ["indie", "singer-songwriter", "chillout"],
    "young_adult": ["indie pop", "pop punk", "alternative"],
    "children": ["acoustic", "folk", "happy"],
    "biography": ["singer-songwriter", "folk", "soul"],
    "memoir": ["soul", "singer-songwriter", "jazz"],
    "history": ["classical", "world", "folk"],
    "science": ["ambient", "minimal", "idm"],
    "philosophy": ["ambient", "post-rock", "classical"],
    "poetry": ["singer-songwriter", "ambient", "jazz"],
    "self_help": ["chillout", "acoustic", "happy"],
    "business": ["electronic", "chillout", "jazz"],
    "travel": ["world", "reggae", "bossa nova"],
    "true_crime": ["trip-hop", "industrial", "blues"],
    "adventure": ["soundtrack", "rock", "folk"],
    "classics": ["classical", "opera", "jazz"],
    "graphic_novel": ["rock", "punk", "electronic"],
    "short_stories": ["indie", "jazz", "chillout"],
    "dystopian": ["industrial", "dark ambient", "electronic"],
    "fiction": ["indie", "chillout", "acoustic"],
    "nonfiction": ["chillout", "ambient", "jazz"],
}

# Mood -> curation tags. Mood carries more weight than genre in tag selection,
# since it is the more discriminating signal.
MOOD_TO_MUSIC_TAGS: dict[str, list[str]] = {
    "dark": ["dark ambient", "darkwave"],
    "uplifting": ["feel good", "uplifting"],
    "melancholic": ["melancholy", "sad"],
    "tense": ["industrial", "dark electro"],
    "whimsical": ["twee", "quirky"],
    "romantic": ["romantic", "love"],
    "contemplative": ["ambient", "instrumental"],
    "epic": ["epic", "post-rock"],
    "cozy": ["mellow", "acoustic"],
    "mysterious": ["trip-hop", "dark jazz"],
    "humorous": ["fun", "happy"],
    "bittersweet": ["bittersweet", "nostalgia"],
}


# Playlist title patterns, chosen by dominant genre.
PLAYLIST_NAME_TEMPLATES: dict[str, str] = {
    "fantasy": "{title} — An Epic Soundtrack",
    "science_fiction": "{title} — Signals from Elsewhere",
    "mystery": "{title} — Clues & Shadows",
    "thriller": "{title} — Pulse and Pursuit",
    "horror": "{title} — After Dark",
    "romance": "{title} — Hearts & Strings",
    "historical_fiction": "{title} — Echoes of the Past",
    "literary_fiction": "{title} — Quiet Reading Hours",
    "young_adult": "{title} — Coming of Age Mix",
    "children": "{title} — Storytime Tunes",
    "poetry": "{title} — Between the Lines",
    "philosophy": "{title} — Slow Thoughts",
    "adventure": "{title} — The Journey Out",
    "dystopian": "{title} — Static & Ruin",
}
DEFAULT_NAME_TEMPLATE = "{title} — A Reading Soundtrack"

DESCRIPTION_TEMPLATES: dict[str, str] = {
    "fantasy": "Sweeping, orchestral music for the world {author} built.",
    "science_fiction": "Cold synths and open space to read {title} by.",
    "mystery": "Smoky, unhurried tracks for following the trail through {title}.",
    "thriller": "Driving, restless music to match the pace of {title}.",
    "horror": "Unsettling atmospheres for the darker corners of {title}.",
    "romance": "Warm, close music for the quieter moments in {title}.",
    "historical_fiction": "Period-tinged pieces for the world of {title}.",
    "literary_fiction": "Understated music that stays out of the prose's way.",
    "young_adult": "Bright, restless songs for growing up alongside {title}.",
    "children": "Gentle, playful music for reading {title} together.",
}
DEFAULT_DESCRIPTION = "A soundtrack built around the mood of {title} by {author}."


def music_genres_for_book(genres: list[str], limit: int = 3) -> list[str]:
    """Collect music genres for a book's genres, preserving priority order."""
    out: list[str] = []
    for genre in genres or []:
        for music_genre in GENRE_TO_MUSIC.get(genre, []):
            if music_genre not in out:
                out.append(music_genre)
            if len(out) >= limit:
                return out
    return out or ["chill", "ambient"][:limit]


def audio_feature_targets(moods: dict[str, float]) -> dict[str, float]:
    """Blend per-mood targets, weighted by mood strength."""
    if not moods:
        return {}
    top = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:3]

    accumulated: dict[str, float] = {}
    total_weight: dict[str, float] = {}
    for mood, strength in top:
        for feature, value in MOOD_TO_AUDIO_FEATURES.get(mood, {}).items():
            accumulated[feature] = accumulated.get(feature, 0.0) + value * strength
            total_weight[feature] = total_weight.get(feature, 0.0) + strength

    return {
        feature: round(value / total_weight[feature], 3)
        for feature, value in accumulated.items()
        if total_weight.get(feature)
    }


def music_tags_for_book(
    genres: list[str], moods: dict[str, float], limit: int = 4
) -> list[str]:
    """Pick the curation tags to pull tracks from, mood first.

    Mood leads because it's the more discriminating signal: two fantasy novels
    with opposite moods should not get the same playlist, whereas the genre tag
    alone would give both "soundtrack".
    """
    tags: list[str] = []
    top_moods = sorted((moods or {}).items(), key=lambda kv: kv[1], reverse=True)[:2]
    for mood, _ in top_moods:
        tags.extend(MOOD_TO_MUSIC_TAGS.get(mood, []))

    for genre in genres or []:
        tags.extend(GENRE_TO_MUSIC_TAGS.get(genre, []))

    deduped = list(dict.fromkeys(tags))[:limit]
    # A book with no recognised genre or mood still needs something playable.
    return deduped or ["chillout", "ambient"][:limit]


def search_terms_for_book(genres: list[str], moods: dict[str, float]) -> list[str]:
    """Build the search queries used when audio-feature targeting is off."""
    terms: list[str] = []
    top_moods = sorted((moods or {}).items(), key=lambda kv: kv[1], reverse=True)[:2]
    for mood, _ in top_moods:
        terms.extend(MOOD_SEARCH_TERMS.get(mood, [])[:2])
    terms.extend(music_genres_for_book(genres, limit=3))
    # Dedupe, preserve order.
    return list(dict.fromkeys(terms))[:6]
