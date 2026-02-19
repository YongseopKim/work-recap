"""Batch job state persistence for crash recovery.

Tracks active batch jobs so interrupted runs can resume polling
instead of re-submitting.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"completed", "failed", "expired"}


class BatchStateStore:
    """Persists batch job state to a JSON file.

    Storage format: {batch_id: {provider, task, custom_ids, submitted_at, status}}
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def save_job(
        self,
        batch_id: str,
        provider: str,
        task: str,
        custom_ids: list[str],
    ) -> None:
        """Record a newly submitted batch job."""
        with self._lock:
            self._data[batch_id] = {
                "batch_id": batch_id,
                "provider": provider,
                "task": task,
                "custom_ids": custom_ids,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "status": "submitted",
            }
            self._persist()

    def get_job(self, batch_id: str) -> dict | None:
        """Get a specific job by ID."""
        with self._lock:
            return self._data.get(batch_id)

    def get_active_jobs(self) -> list[dict]:
        """Return jobs that are not in a terminal status."""
        with self._lock:
            return [
                job for job in self._data.values() if job.get("status") not in _TERMINAL_STATUSES
            ]

    def update_status(self, batch_id: str, status: str) -> None:
        """Update the status of a batch job."""
        with self._lock:
            if batch_id in self._data:
                self._data[batch_id]["status"] = status
                self._persist()

    def remove_job(self, batch_id: str) -> None:
        """Remove a batch job record."""
        with self._lock:
            self._data.pop(batch_id, None)
            self._persist()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load batch state from %s: %s", self._path, e)
                self._data = {}
        else:
            self._data = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
