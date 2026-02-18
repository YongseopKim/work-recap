"""Chunk search result caching for resumable fetch_range()."""

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class FetchProgressStore:
    """Saves per-chunk search results so interrupted fetch_range() can resume
    without re-executing search API calls.

    Storage layout:
        {progress_dir}/{sanitized_chunk_key}.json
    """

    def __init__(self, progress_dir: Path) -> None:
        self._dir = progress_dir

    def _key_to_path(self, chunk_key: str) -> Path:
        """Sanitize chunk key for filesystem safety."""
        safe = chunk_key.replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe}.json"

    def save_chunk_search(self, chunk_key: str, buckets: dict) -> None:
        """Persist search results for a chunk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._key_to_path(chunk_key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(buckets, f)
        logger.debug("Saved chunk search: %s → %s", chunk_key, path)

    def load_chunk_search(self, chunk_key: str) -> dict | None:
        """Load cached search results, or None if not cached."""
        path = self._key_to_path(chunk_key)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("Loaded chunk search: %s", chunk_key)
        return data

    def clear_chunk(self, chunk_key: str) -> None:
        """Remove cached data for a single chunk."""
        path = self._key_to_path(chunk_key)
        if path.exists():
            path.unlink()
            logger.debug("Cleared chunk: %s", chunk_key)

    def clear_all(self) -> None:
        """Remove all cached chunk data."""
        if self._dir.exists():
            shutil.rmtree(self._dir)
            logger.debug("Cleared all fetch progress")
