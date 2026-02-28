"""스케줄러 실행 이력 관리 -- JSON 파일 기반."""

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path

from workrecap.scheduler.notifier import SchedulerEvent

logger = logging.getLogger(__name__)

_DEFAULT_MAX = 100


class SchedulerHistory:
    def __init__(self, path: Path, max_entries: int = _DEFAULT_MAX) -> None:
        self._path = path
        self._max = max_entries
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        if not self._path or not self._path.exists():
            return []
        with open(self._path) as f:
            return json.load(f)

    def _save(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def record(self, event: SchedulerEvent) -> None:
        with self._lock:
            entries = self._load()
            entries.append(asdict(event))
            if len(entries) > self._max:
                entries = entries[-self._max :]
            self._save(entries)

    def list(self, job: str | None = None, limit: int | None = None) -> list[dict]:
        entries = self._load()
        if job:
            entries = [e for e in entries if e["job"] == job]
        if limit:
            entries = entries[-limit:]
        return entries
