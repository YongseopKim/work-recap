"""DailyStateStore tests — per-date timestamp state for staleness detection."""

from datetime import datetime, timezone

import pytest

from git_recap.services.daily_state import DailyStateStore


@pytest.fixture
def store(tmp_path):
    return DailyStateStore(tmp_path / "daily_state.json")


# ── get/set round-trip ──


class TestGetSet:
    def test_get_nonexistent_returns_none(self, store):
        assert store.get_timestamp("fetch", "2025-02-16") is None

    def test_set_and_get_round_trip(self, store):
        ts = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts)
        result = store.get_timestamp("fetch", "2025-02-16")
        assert result == ts

    def test_set_default_now(self, store):
        store.set_timestamp("fetch", "2025-02-16")
        result = store.get_timestamp("fetch", "2025-02-16")
        assert result is not None
        assert isinstance(result, datetime)

    def test_multiple_phases_same_date(self, store):
        ts1 = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts1)
        store.set_timestamp("normalize", "2025-02-16", ts2)
        assert store.get_timestamp("fetch", "2025-02-16") == ts1
        assert store.get_timestamp("normalize", "2025-02-16") == ts2

    def test_multiple_dates(self, store):
        ts1 = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 18, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts1)
        store.set_timestamp("fetch", "2025-02-17", ts2)
        assert store.get_timestamp("fetch", "2025-02-16") == ts1
        assert store.get_timestamp("fetch", "2025-02-17") == ts2

    def test_overwrite(self, store):
        ts1 = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 17, 15, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts1)
        store.set_timestamp("fetch", "2025-02-16", ts2)
        assert store.get_timestamp("fetch", "2025-02-16") == ts2


# ── Persistence ──


class TestPersistence:
    def test_persists_to_file(self, tmp_path):
        path = tmp_path / "daily_state.json"
        store1 = DailyStateStore(path)
        ts = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        store1.set_timestamp("fetch", "2025-02-16", ts)

        # New store instance reads from file
        store2 = DailyStateStore(path)
        assert store2.get_timestamp("fetch", "2025-02-16") == ts

    def test_empty_file_does_not_exist(self, tmp_path):
        path = tmp_path / "daily_state.json"
        store = DailyStateStore(path)
        assert store.get_timestamp("fetch", "2025-02-16") is None
        assert not path.exists()

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "subdir" / "deep" / "daily_state.json"
        store = DailyStateStore(path)
        store.set_timestamp("fetch", "2025-02-16")
        assert path.exists()


# ── is_fetch_stale ──


class TestIsFetchStale:
    def test_no_record_is_stale(self, store):
        assert store.is_fetch_stale("2025-02-16") is True

    def test_fetched_same_day_is_stale(self, store):
        """fetched_at.date() == target_date → stale (could be outdated by evening)."""
        ts = datetime(2025, 2, 16, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts)
        assert store.is_fetch_stale("2025-02-16") is True

    def test_fetched_next_day_is_final(self, store):
        """fetched_at.date() > target_date → final (not stale)."""
        ts = datetime(2025, 2, 17, 8, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts)
        assert store.is_fetch_stale("2025-02-16") is False

    def test_fetched_before_target_is_stale(self, store):
        """fetched_at.date() < target_date → stale."""
        ts = datetime(2025, 2, 15, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts)
        assert store.is_fetch_stale("2025-02-16") is True


# ── is_normalize_stale (cascade) ──


class TestIsNormalizeStale:
    def test_no_record_is_stale(self, store):
        assert store.is_normalize_stale("2025-02-16") is True

    def test_no_fetch_record_is_stale(self, store):
        """normalize_ts exists but no fetch_ts → stale."""
        ts = datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("normalize", "2025-02-16", ts)
        assert store.is_normalize_stale("2025-02-16") is True

    def test_fetch_newer_than_normalize_is_stale(self, store):
        """fetch_ts > normalize_ts → stale (re-fetched data)."""
        store.set_timestamp(
            "fetch", "2025-02-16", datetime(2025, 2, 17, 15, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        assert store.is_normalize_stale("2025-02-16") is True

    def test_fetch_equal_to_normalize_is_not_stale(self, store):
        """fetch_ts == normalize_ts → not stale."""
        ts = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("fetch", "2025-02-16", ts)
        store.set_timestamp("normalize", "2025-02-16", ts)
        assert store.is_normalize_stale("2025-02-16") is False

    def test_normalize_newer_than_fetch_is_not_stale(self, store):
        """normalize_ts > fetch_ts → not stale."""
        store.set_timestamp(
            "fetch", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        )
        assert store.is_normalize_stale("2025-02-16") is False


# ── is_summarize_stale (cascade) ──


class TestIsSummarizeStale:
    def test_no_record_is_stale(self, store):
        assert store.is_summarize_stale("2025-02-16") is True

    def test_no_normalize_record_is_stale(self, store):
        """summarize_ts exists but no normalize_ts → stale."""
        ts = datetime(2025, 2, 17, 12, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("summarize", "2025-02-16", ts)
        assert store.is_summarize_stale("2025-02-16") is True

    def test_normalize_newer_than_summarize_is_stale(self, store):
        """normalize_ts > summarize_ts → stale (re-normalized data)."""
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 15, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "summarize", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        assert store.is_summarize_stale("2025-02-16") is True

    def test_normalize_equal_to_summarize_is_not_stale(self, store):
        """normalize_ts == summarize_ts → not stale."""
        ts = datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        store.set_timestamp("normalize", "2025-02-16", ts)
        store.set_timestamp("summarize", "2025-02-16", ts)
        assert store.is_summarize_stale("2025-02-16") is False

    def test_summarize_newer_than_normalize_is_not_stale(self, store):
        """summarize_ts > normalize_ts → not stale."""
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "summarize", "2025-02-16", datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        )
        assert store.is_summarize_stale("2025-02-16") is False


# ── stale_dates ──


class TestStaleDates:
    def test_all_stale(self, store):
        """No records → all stale."""
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        result = store.stale_dates("fetch", dates)
        assert result == dates

    def test_some_stale(self, store):
        """Mix of stale and not-stale."""
        # Feb 15: fetched on Feb 16 → final (not stale)
        store.set_timestamp(
            "fetch", "2025-02-15", datetime(2025, 2, 16, 8, 0, 0, tzinfo=timezone.utc)
        )
        # Feb 14 and Feb 16: no record → stale
        dates = ["2025-02-14", "2025-02-15", "2025-02-16"]
        result = store.stale_dates("fetch", dates)
        assert result == ["2025-02-14", "2025-02-16"]

    def test_none_stale(self, store):
        """All fetched after target_date → none stale."""
        for d in ["2025-02-14", "2025-02-15"]:
            store.set_timestamp("fetch", d, datetime(2025, 2, 20, 8, 0, 0, tzinfo=timezone.utc))
        result = store.stale_dates("fetch", ["2025-02-14", "2025-02-15"])
        assert result == []

    def test_normalize_cascade(self, store):
        """stale_dates('normalize') uses cascade logic."""
        # Feb 15: fetch and normalize done, but fetch is newer
        store.set_timestamp(
            "fetch", "2025-02-15", datetime(2025, 2, 17, 15, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "normalize", "2025-02-15", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        # Feb 16: up to date
        store.set_timestamp(
            "fetch", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        )
        result = store.stale_dates("normalize", ["2025-02-15", "2025-02-16"])
        assert result == ["2025-02-15"]

    def test_summarize_cascade(self, store):
        """stale_dates('summarize') uses cascade logic."""
        # Feb 15: normalize newer than summarize
        store.set_timestamp(
            "normalize", "2025-02-15", datetime(2025, 2, 17, 15, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "summarize", "2025-02-15", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        # Feb 16: up to date
        store.set_timestamp(
            "normalize", "2025-02-16", datetime(2025, 2, 17, 10, 0, 0, tzinfo=timezone.utc)
        )
        store.set_timestamp(
            "summarize", "2025-02-16", datetime(2025, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        )
        result = store.stale_dates("summarize", ["2025-02-15", "2025-02-16"])
        assert result == ["2025-02-15"]

    def test_empty_input(self, store):
        result = store.stale_dates("fetch", [])
        assert result == []
