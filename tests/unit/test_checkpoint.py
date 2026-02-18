"""Tests for thread-safe checkpoint utility."""

import json
import threading

from workrecap.services.checkpoint import update_checkpoint


class TestUpdateCheckpoint:
    def test_creates_new_checkpoint(self, tmp_path):
        cp_path = tmp_path / "checkpoints.json"
        update_checkpoint(cp_path, "last_fetch_date", "2025-02-16")

        with open(cp_path) as f:
            data = json.load(f)
        assert data == {"last_fetch_date": "2025-02-16"}

    def test_preserves_existing_keys(self, tmp_path):
        cp_path = tmp_path / "checkpoints.json"
        with open(cp_path, "w") as f:
            json.dump({"last_fetch_date": "2025-02-15"}, f)

        update_checkpoint(cp_path, "last_normalize_date", "2025-02-16")

        with open(cp_path) as f:
            data = json.load(f)
        assert data == {
            "last_fetch_date": "2025-02-15",
            "last_normalize_date": "2025-02-16",
        }

    def test_overwrites_existing_key(self, tmp_path):
        cp_path = tmp_path / "checkpoints.json"
        update_checkpoint(cp_path, "last_fetch_date", "2025-02-15")
        update_checkpoint(cp_path, "last_fetch_date", "2025-02-16")

        with open(cp_path) as f:
            data = json.load(f)
        assert data["last_fetch_date"] == "2025-02-16"

    def test_creates_parent_dirs(self, tmp_path):
        cp_path = tmp_path / "sub" / "dir" / "checkpoints.json"
        update_checkpoint(cp_path, "last_fetch_date", "2025-02-16")
        assert cp_path.exists()

    def test_concurrent_updates(self, tmp_path):
        """Multiple threads updating different keys concurrently."""
        cp_path = tmp_path / "checkpoints.json"
        errors = []

        def writer(key, value):
            try:
                update_checkpoint(cp_path, key, value)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("last_fetch_date", "2025-02-16")),
            threading.Thread(target=writer, args=("last_normalize_date", "2025-02-17")),
            threading.Thread(target=writer, args=("last_summarize_date", "2025-02-18")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

        with open(cp_path) as f:
            data = json.load(f)
        assert data["last_fetch_date"] == "2025-02-16"
        assert data["last_normalize_date"] == "2025-02-17"
        assert data["last_summarize_date"] == "2025-02-18"
