"""Reading-service logic that doesn't need a database."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.reading_service import ReadingService


class TestSpeedCalculation:
    def test_basic_speed(self):
        # 30 pages in 30 minutes = 1 page/min
        assert ReadingService._speed(30, 1800) == pytest.approx(1.0)

    def test_implausible_speed_is_clamped(self):
        """A 1-second 'session' would otherwise record hundreds of pages per
        minute and poison every future estimate."""
        assert ReadingService._speed(300, 1) == 20.0

    def test_zero_inputs_return_none(self):
        assert ReadingService._speed(0, 100) is None
        assert ReadingService._speed(10, 0) is None

    def test_blend_favours_history(self):
        """EMA: one odd session shouldn't redefine a reader's pace."""
        blended = ReadingService._blend_speed(1.0, pages=60, seconds=1800)
        assert 1.0 < blended < 2.0

    def test_blend_with_no_history_uses_session(self):
        assert ReadingService._blend_speed(None, 30, 1800) == pytest.approx(1.0)

    def test_blend_ignores_unusable_session(self):
        assert ReadingService._blend_speed(1.5, 0, 0) == 1.5


class TestAnnotationMerge:
    def test_union_not_replace(self):
        """A device syncing a stale list must not delete another device's
        annotations."""
        existing = [{"page": 10, "text": "from phone"}]
        incoming = [{"page": 20, "text": "from tablet"}]
        merged = ReadingService._merge_annotations(existing, incoming, "text")
        assert len(merged) == 2

    def test_identical_annotations_deduplicated(self):
        note = {"page": 10, "text": "same"}
        merged = ReadingService._merge_annotations([note], [dict(note)], "text")
        assert len(merged) == 1

    def test_sorted_by_page(self):
        merged = ReadingService._merge_annotations(
            [{"page": 30, "text": "c"}],
            [{"page": 10, "text": "a"}, {"page": 20, "text": "b"}],
            "text",
        )
        assert [m["page"] for m in merged] == [10, 20, 30]

    def test_created_at_added_when_missing(self):
        merged = ReadingService._merge_annotations([], [{"page": 1, "text": "x"}], "text")
        assert "created_at" in merged[0]

    def test_handles_none_inputs(self):
        assert ReadingService._merge_annotations(None, None, "text") == []


class TestStreakLogic:
    """Streak maths, exercised through the same date arithmetic the service
    uses. Verifies the rules rather than the query."""

    @staticmethod
    def _streak(days_ago_list, today=None):
        today = today or datetime.now(UTC).date()
        days = sorted({today - timedelta(days=d) for d in days_ago_list}, reverse=True)
        if not days:
            return 0
        current = 0
        if days[0] in (today, today - timedelta(days=1)):
            current = 1
            for prev, nxt in zip(days, days[1:], strict=False):
                if (prev - nxt).days == 1:
                    current += 1
                else:
                    break
        return current

    def test_consecutive_days_counted(self):
        assert self._streak([0, 1, 2, 3]) == 4

    def test_yesterday_still_counts(self):
        """The streak breaks only after a full missed day — otherwise it
        resets every morning before the user has read."""
        assert self._streak([1, 2, 3]) == 3

    def test_gap_breaks_the_streak(self):
        assert self._streak([0, 1, 3, 4]) == 2

    def test_stale_activity_is_no_streak(self):
        assert self._streak([5, 6, 7]) == 0

    def test_no_activity(self):
        assert self._streak([]) == 0
