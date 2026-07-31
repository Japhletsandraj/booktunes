"""Music mapping and playlist assembly (no network)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.music import genre_mapping
from app.services.music.deezer_client import DeezerClient
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


class TestCurationTags:
    def test_mood_leads_over_genre(self):
        """Mood is the discriminating signal — two fantasy books with opposite
        moods must not get identical tags."""
        dark = genre_mapping.music_tags_for_book(["fantasy"], {"dark": 0.9})
        cosy = genre_mapping.music_tags_for_book(["fantasy"], {"cozy": 0.9})
        assert dark != cosy
        assert dark[0] in genre_mapping.MOOD_TO_MUSIC_TAGS["dark"]

    def test_genre_used_when_no_mood(self):
        tags = genre_mapping.music_tags_for_book(["horror"], {})
        assert "dark ambient" in tags

    def test_unmapped_input_still_yields_tags(self):
        assert genre_mapping.music_tags_for_book(["basket_weaving"], {})
        assert genre_mapping.music_tags_for_book([], {})

    def test_deduped_and_capped(self):
        tags = genre_mapping.music_tags_for_book(
            ["horror", "dystopian", "thriller"], {"dark": 0.8, "tense": 0.7}, limit=4
        )
        assert len(tags) == 4
        assert len(set(tags)) == 4

    def test_every_mood_has_tags(self):
        """A mood present in MOOD_SEARCH_TERMS but missing here would silently
        contribute nothing to tag selection."""
        assert set(genre_mapping.MOOD_TO_MUSIC_TAGS) == set(
            genre_mapping.MOOD_SEARCH_TERMS
        )


class TestDeezerParsing:
    def test_nested_artist_object(self):
        parsed = DeezerClient._parse_name_pair(
            {"title": "Teardrop", "artist": {"name": "Massive Attack"}}
        )
        assert parsed == {"title": "Teardrop", "artist": "Massive Attack"}

    def test_bare_artist_string(self):
        """The playlist and search endpoints disagree on this shape."""
        parsed = DeezerClient._parse_name_pair(
            {"title": "Teardrop", "artist": "Massive Attack"}
        )
        assert parsed == {"title": "Teardrop", "artist": "Massive Attack"}

    def test_missing_fields_rejected(self):
        assert DeezerClient._parse_name_pair({"title": "", "artist": "x"}) is None
        assert DeezerClient._parse_name_pair({"title": "x"}) is None
        assert DeezerClient._parse_name_pair("not a dict") is None


class TestDeezerPlaylistRanking:
    """Deezer's playlist search is fuzzy, so relevance ordering is what keeps
    a tag's results on-tag rather than on the current pop charts."""

    def test_title_match_beats_unrelated(self):
        on_tag = {"title": "Trip-Hop Essentials", "user": {"name": "someone"}}
        off_tag = {"title": "Summer Hits 2026", "user": {"name": "someone"}}
        assert DeezerClient._relevance(on_tag, "trip-hop") > DeezerClient._relevance(
            off_tag, "trip-hop"
        )

    def test_editorial_playlists_preferred(self):
        editorial = {"title": "Mixed Bag", "user": {"name": "Deezer Jazz Editor"}}
        personal = {"title": "Mixed Bag", "user": {"name": "someuser"}}
        assert DeezerClient._relevance(editorial, "jazz") > DeezerClient._relevance(
            personal, "jazz"
        )

    def test_missing_fields_do_not_raise(self):
        assert DeezerClient._relevance({}, "ambient") == 0

    def test_short_words_ignored(self):
        """Two-letter fragments match almost any title — they would make the
        score meaningless."""
        assert DeezerClient._relevance({"title": "so far so good"}, "so") == 0


class TestDeezerInterleave:
    def test_caps_tracks_per_artist(self):
        """One artist-focused playlist must not fill a whole tag."""
        pool = [{"artist": "One Band", "title": f"Song {i}"} for i in range(10)]
        assert len(DeezerClient._interleave([pool], cap=10)) == 2

    def test_round_robins_across_playlists(self):
        pools = [
            [{"artist": "A1", "title": "1"}, {"artist": "A2", "title": "2"}],
            [{"artist": "B1", "title": "1"}, {"artist": "B2", "title": "2"}],
        ]
        out = DeezerClient._interleave(pools, cap=4)
        assert [p["artist"] for p in out] == ["A1", "B1", "A2", "B2"]

    def test_dedupes_across_playlists(self):
        pools = [
            [{"artist": "Massive Attack", "title": "Teardrop"}],
            [{"artist": "massive attack", "title": "teardrop"}],
        ]
        assert len(DeezerClient._interleave(pools, cap=10)) == 1

    def test_empty_pools(self):
        assert DeezerClient._interleave([], cap=10) == []
        assert DeezerClient._interleave([[]], cap=10) == []


class TestPairInterleaving:
    def test_round_robins_across_tags(self):
        pools = [
            [{"artist": "A", "title": "1"}, {"artist": "A", "title": "2"}],
            [{"artist": "B", "title": "1"}, {"artist": "B", "title": "2"}],
        ]
        out = MusicService._interleave_pairs(pools, cap=4)
        assert [p["artist"] for p in out] == ["A", "B", "A", "B"]

    def test_dedupes_case_insensitively(self):
        pools = [
            [{"artist": "Massive Attack", "title": "Teardrop"}],
            [{"artist": "massive attack", "title": "teardrop"}],
        ]
        assert len(MusicService._interleave_pairs(pools, cap=10)) == 1

    def test_respects_cap(self):
        pools = [[{"artist": f"A{i}", "title": str(i)} for i in range(20)]]
        assert len(MusicService._interleave_pairs(pools, cap=5)) == 5

    def test_handles_uneven_and_empty_pools(self):
        pools = [[{"artist": "A", "title": "1"}], [], [{"artist": "B", "title": "1"}]]
        assert len(MusicService._interleave_pairs(pools, cap=10)) == 2

    def test_no_pools(self):
        assert MusicService._interleave_pairs([], cap=10) == []


class TestCurationResolution:
    @pytest.fixture(autouse=True)
    def _clear_latch(self):
        DeezerClient.reset()
        yield
        DeezerClient.reset()

    async def test_returns_empty_when_latched_off(self):
        """An unreachable Deezer must degrade to [] so the YouTube fallback
        takes over — never raise."""
        with patch.object(DeezerClient, "is_configured", return_value=False):
            assert await MusicService().get_curated_tracks(["ambient"], limit=5) == []

    async def test_repeated_failures_latch_the_client_off(self):
        """A network block would otherwise cost every playlist generation a
        full round of timeouts."""
        with patch(
            "app.services.music.deezer_client.fetch_json",
            AsyncMock(side_effect=RuntimeError("network down")),
        ):
            for _ in range(10):
                await DeezerClient.tag_top_tracks("ambient", limit=5)

        assert DeezerClient.is_configured() is False

    async def test_missing_playlist_does_not_latch(self):
        """A 404 is a deleted playlist, not an outage — latching on it would
        disable discovery over normal catalogue churn."""
        with patch(
            "app.services.music.deezer_client.fetch_json",
            AsyncMock(return_value=None),
        ):
            for _ in range(10):
                await DeezerClient.tag_top_tracks("ambient", limit=5)

        assert DeezerClient.is_configured() is True

    async def test_quota_error_backs_off_without_latching(self):
        """Deezer's quota window is 5s wide — a burst must retry, not disable
        discovery for the whole process."""
        quota = {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}
        with (
            patch(
                "app.services.music.deezer_client.fetch_json",
                AsyncMock(return_value=quota),
            ) as fetch,
            patch("app.services.music.deezer_client.asyncio.sleep", AsyncMock()),
        ):
            assert await DeezerClient.tag_top_tracks("ambient", limit=5) == []

        # Retried rather than giving up on the first quota response.
        assert fetch.await_count > 1
        assert DeezerClient.is_configured() is True

    async def test_names_resolved_through_youtube(self):
        pairs = [{"artist": "Massive Attack", "title": "Teardrop"}]
        yt_track = {
            "id": "vid1", "title": "Teardrop", "artist": "Massive Attack",
            "album": "Mezzanine", "duration_ms": 1000, "preview_url": None,
            "external_url": "https://music.youtube.com/watch?v=vid1",
            "artwork_url": None, "source": "youtube_music",
        }
        with (
            patch.object(DeezerClient, "is_configured", return_value=True),
            patch.object(DeezerClient, "tag_top_tracks", AsyncMock(return_value=pairs)),
            patch.object(
                MusicService, "get_youtube_tracks", AsyncMock(return_value=[yt_track])
            ),
        ):
            tracks = await MusicService().get_curated_tracks(["trip-hop"], limit=5)

        assert len(tracks) == 1
        # Playable id comes from YouTube; the track is playable, not a name.
        assert tracks[0]["id"] == "vid1"
        assert tracks[0]["source"] == "youtube_music"
        # Deezer's spelling is retained for provenance.
        assert tracks[0]["curated_artist"] == "Massive Attack"

    async def test_unresolvable_names_dropped_not_fatal(self):
        pairs = [{"artist": "Obscure", "title": "Nothing"}]
        with (
            patch.object(DeezerClient, "is_configured", return_value=True),
            patch.object(DeezerClient, "tag_top_tracks", AsyncMock(return_value=pairs)),
            patch.object(
                MusicService, "get_youtube_tracks", AsyncMock(return_value=[])
            ),
        ):
            assert await MusicService().get_curated_tracks(["ambient"], limit=5) == []

    async def test_no_tags_short_circuits(self):
        """Must not spend a Deezer call on an empty tag list."""
        mock = AsyncMock(return_value=[])
        with (
            patch.object(DeezerClient, "is_configured", return_value=True),
            patch.object(DeezerClient, "tag_top_tracks", mock),
        ):
            assert await MusicService().get_curated_tracks([], limit=5) == []
        mock.assert_not_called()


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


def _book(**overrides):
    from types import SimpleNamespace

    return SimpleNamespace(
        **{
            "title": "The Hobbit",
            "author": "Tolkien",
            "genres": ["fantasy"],
            "moods": {"epic": 0.9},
            **overrides,
        }
    )


def _tracks(n, prefix="t", source="youtube_music"):
    return [
        {
            "id": f"{prefix}{i}", "title": f"Track {i}", "artist": f"Artist {i}",
            "album": None, "duration_ms": 1000, "preview_url": None,
            "external_url": None, "artwork_url": None, "source": source,
        }
        for i in range(n)
    ]


class TestPlaylistSourceSelection:
    """The orchestration in generate_book_playlist — which source wins."""

    async def test_deezer_is_primary(self):
        with (
            patch.object(
                MusicService, "get_curated_tracks", AsyncMock(return_value=_tracks(20))
            ),
            patch.object(MusicService, "get_youtube_tracks", AsyncMock()) as yt,
        ):
            result = await MusicService().generate_book_playlist(_book(), track_count=20)

        assert result["source"] == "deezer"
        assert len(result["tracks"]) == 20
        # Keyword fallback must not run when curation succeeded.
        yt.assert_not_called()

    async def test_falls_back_to_youtube_when_deezer_empty(self):
        """The no-API-key path: everything still works, just uncurated."""
        with (
            patch.object(
                MusicService, "get_curated_tracks", AsyncMock(return_value=[])
            ),
            patch.object(
                MusicService, "get_spotify_recommendations", AsyncMock(return_value=[])
            ),
            patch.object(
                MusicService, "search_spotify_tracks", AsyncMock(return_value=[])
            ),
            patch.object(
                MusicService, "get_youtube_tracks", AsyncMock(return_value=_tracks(15))
            ),
        ):
            result = await MusicService().generate_book_playlist(_book(), track_count=20)

        assert result["source"] == "youtube_music"
        assert result["tracks"]

    async def test_spotify_not_consulted_when_deezer_succeeds(self):
        """Spotify is retired to optional — a working curation path must not
        spend a call on it."""
        with (
            patch.object(
                MusicService, "get_curated_tracks", AsyncMock(return_value=_tracks(20))
            ),
            patch.object(
                MusicService, "get_spotify_recommendations", AsyncMock()
            ) as recs,
            patch.object(MusicService, "search_spotify_tracks", AsyncMock()) as search,
        ):
            await MusicService().generate_book_playlist(_book(), track_count=20)

        recs.assert_not_called()
        search.assert_not_called()

    async def test_force_source_youtube_skips_deezer(self):
        with (
            patch.object(MusicService, "get_curated_tracks", AsyncMock()) as curated,
            patch.object(
                MusicService, "get_youtube_tracks", AsyncMock(return_value=_tracks(15))
            ),
        ):
            result = await MusicService().generate_book_playlist(
                _book(), track_count=20, force_source="youtube_music"
            )

        curated.assert_not_called()
        assert result["source"] == "youtube_music"

    async def test_returns_none_when_every_source_dry(self):
        with (
            patch.object(MusicService, "get_curated_tracks", AsyncMock(return_value=[])),
            patch.object(
                MusicService, "get_spotify_recommendations", AsyncMock(return_value=[])
            ),
            patch.object(
                MusicService, "search_spotify_tracks", AsyncMock(return_value=[])
            ),
            patch.object(MusicService, "get_youtube_tracks", AsyncMock(return_value=[])),
        ):
            result = await MusicService().generate_book_playlist(_book())

        assert result is None

    async def test_book_without_genres_or_moods_still_builds(self):
        """The long tail of the catalogue has neither — it must not crash."""
        with patch.object(
            MusicService, "get_curated_tracks", AsyncMock(return_value=_tracks(20))
        ):
            result = await MusicService().generate_book_playlist(
                _book(genres=[], moods={}), track_count=20
            )

        assert result is not None
        assert result["source"] == "deezer"


class TestPreviews:
    async def test_youtube_source_reports_unavailable_with_reason(self):
        results = await MusicService().get_previews(["a", "b"], source="youtube_music")
        assert len(results) == 2
        assert all(r["available"] is False for r in results)
        assert all("external_url" in r["reason"] for r in results)

    async def test_deezer_source_reports_unavailable(self):
        results = await MusicService().get_previews(["a"], source="deezer")
        assert results[0]["available"] is False
        assert results[0]["preview_url"] is None
