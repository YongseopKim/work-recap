"""Tests for FetchProgressStore — chunk search result caching for resumable fetching."""

import pytest

from git_recap.services.fetch_progress import FetchProgressStore


@pytest.fixture
def store(tmp_path):
    return FetchProgressStore(tmp_path / "fetch_progress")


class TestFetchProgressStore:
    def test_save_and_load_round_trip(self, store):
        """Save chunk search results and load them back."""
        buckets = {
            "2025-02-16": {
                "prs": {"url1": {"title": "PR1"}},
                "commits": [{"sha": "abc"}],
                "issues": {},
            }
        }
        store.save_chunk_search("2025-02__2025-02", buckets)

        loaded = store.load_chunk_search("2025-02__2025-02")
        assert loaded is not None
        assert loaded["2025-02-16"]["prs"]["url1"]["title"] == "PR1"
        assert loaded["2025-02-16"]["commits"][0]["sha"] == "abc"

    def test_load_nonexistent_returns_none(self, store):
        """Loading a chunk that was never saved returns None."""
        assert store.load_chunk_search("nonexistent") is None

    def test_clear_chunk(self, store):
        """clear_chunk removes saved data for a specific chunk."""
        store.save_chunk_search("chunk1", {"2025-02-16": {}})
        store.save_chunk_search("chunk2", {"2025-02-17": {}})

        store.clear_chunk("chunk1")

        assert store.load_chunk_search("chunk1") is None
        assert store.load_chunk_search("chunk2") is not None

    def test_clear_all(self, store):
        """clear_all removes all saved chunk data."""
        store.save_chunk_search("chunk1", {"2025-02-16": {}})
        store.save_chunk_search("chunk2", {"2025-02-17": {}})

        store.clear_all()

        assert store.load_chunk_search("chunk1") is None
        assert store.load_chunk_search("chunk2") is None

    def test_creates_directory(self, tmp_path):
        """Store creates its directory on first save."""
        progress_dir = tmp_path / "deep" / "progress"
        store = FetchProgressStore(progress_dir)
        store.save_chunk_search("chunk1", {"2025-02-16": {}})
        assert progress_dir.exists()

    def test_chunk_key_sanitized(self, store):
        """Chunk keys with slashes are sanitized for filesystem safety."""
        store.save_chunk_search("2025-02/01__2025-02/28", {"data": "value"})
        loaded = store.load_chunk_search("2025-02/01__2025-02/28")
        assert loaded is not None
