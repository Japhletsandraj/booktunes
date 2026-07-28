"""Music mapping and playlist assembly (no network)."""


from app.services.music import genre_mapping
from app.services.music.music_service import MusicService


class TestGenreMapping:
    def test_known_genre_maps_to_music(self):
        assert "soundtracks" in genre_mapping.music_genres_for_book(["fantasy"])

    def test_unknown_genre_falls_back(self):
        """An unmapped genre must still yield seeds, or playlist generation
        fails for the long tail of the catalogue."""
        result = genre_mapping.music_genres_for_book(["underwater_basket_weaving"])
        assert result

    def test_empty_genres_falls_back(self):
        assert genre_mapping.music_genres_for_book([])

    def test_respects_limit_and_dedupes(self):
        result = genre_mapping.music_genres_for_book(
            ["fantasy", "adventure", "classics"], limit=3
        )
        assert len(result) == 3
        assert len(set(result)) == 3

    def test_at_least_twenty_genres_mapped(self):
        assert len(genre_mapping.GENRE_TO_MUSIC) >= 20


class TestAudioTargets:
    def test_blend_weighted_by_mood_strength(self):
        strong_dark = genre_mapping.audio_feature_targets({"dark": 1.0, "uplifting": 0.1})
        strong_bright = genre_mapping.audio_feature_targets({"dark": 0.1, "uplifting": 1.0})
        assert strong_dark["target_valence"] < strong_bright["target_valence"]

    def test_empty_moods(self):
        assert genre_mapping.audio_feature_targets({}) == {}

    def test_values_stay_in_range(self):
        targets = genre_mapping.audio_feature_targets(
            {"dark": 0.9, "tense": 0.8, "epic": 0.7}
        )
        for key, value in targets.items():
            if key in ("target_valence", "target_energy", "target_acousticness"):
                assert 0.0 <= value <= 1.0


class TestSearchTerms:
    def test_combines_mood_and_genre_terms(self):
        terms = genre_mapping.search_terms_for_book(["fantasy"], {"epic": 0.9})
        assert any("epic" in t for t in terms)
        assert "soundtracks" in terms

    def test_deduped_and_capped(self):
        terms = genre_mapping.search_terms_for_book(
            ["fantasy", "adventure"], {"epic": 0.9, "dark": 0.8}
        )
        assert len(terms) == len(set(terms))
        assert len(terms) <= 6


class TestTrackSelection:
    @staticmethod
    def _pool(prefix, n, artist=None):
        return [
            {"id": f"{prefix}{i}", "title": f"Song {i}",
             "artist": artist or f"Artist {prefix}{i}"}
            for i in range(n)
        ]

    def test_interleaves_pools(self):
        """Round-robin, not concatenation — otherwise the playlist is all one
        search query's sound."""
        selected = MusicService._select_tracks(
            [self._pool("a", 5), self._pool("b", 5)], target=4
        )
        prefixes = [t["id"][0] for t in selected]
        assert "a" in prefixes and "b" in prefixes

    def test_deduplicates_across_pools(self):
        shared = self._pool("x", 3)
        selected = MusicService._select_tracks([shared, list(shared)], target=6)
        assert len({t["id"] for t in selected}) == len(selected)

    def test_caps_tracks_per_artist(self):
        selected = MusicService._select_tracks(
            [self._pool("a", 10, artist="One Band")], target=10
        )
        assert len(selected) <= 2

    def test_respects_target(self):
        selected = MusicService._select_tracks([self._pool("a", 50)], target=5)
        assert len(selected) == 5

    def test_empty_pools(self):
        assert MusicService._select_tracks([], target=10) == []
        assert MusicService._select_tracks([[]], target=10) == []


class TestMatchScores:
    def test_mood_score_rises_with_completeness(self):
        partial = MusicService._mood_match_score({"epic": 0.9}, found=5, target=20)
        full = MusicService._mood_match_score({"epic": 0.9}, found=20, target=20)
        assert full > partial

    def test_scores_bounded(self):
        assert 0 <= MusicService._mood_match_score({"epic": 1.0}, 20, 20) <= 100
        assert 0 <= MusicService._genre_match_score(["fantasy"], 20, 20) <= 100

    def test_neutral_without_signal(self):
        assert MusicService._mood_match_score({}, 10, 20) == 50.0
        assert MusicService._genre_match_score([], 10, 20) == 50.0


class TestPlaylistMetadata:
    async def test_uses_genre_template(self):
        from types import SimpleNamespace

        service = MusicService()
        book = SimpleNamespace(title="The Hobbit", author="Tolkien", genres=["fantasy"])
        meta = await service.generate_playlist_metadata(book)
        assert "The Hobbit" in meta["name"]
        assert "Epic" in meta["name"]
        assert meta["description"]

    async def test_falls_back_for_unmapped_genre(self):
        from types import SimpleNamespace

        service = MusicService()
        book = SimpleNamespace(title="Odd Book", author="Someone", genres=["business"])
        meta = await service.generate_playlist_metadata(book)
        assert "Odd Book" in meta["name"]

    async def test_long_titles_truncated(self):
        from types import SimpleNamespace

        service = MusicService()
        book = SimpleNamespace(title="T" * 300, author="A", genres=["fiction"])
        meta = await service.generate_playlist_metadata(book)
        assert len(meta["name"]) <= 120
