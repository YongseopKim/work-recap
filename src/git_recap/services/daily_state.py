"""Per-date timestamp state for staleness detection and cascade reprocessing."""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class DailyStateStore:
    """날짜별 fetch/normalize/summarize 타임스탬프 관리.

    Staleness rules:
      - fetch: stale if no record OR fetched_at.date() <= target_date
      - normalize: stale if no record OR fetch_ts > normalize_ts
      - summarize: stale if no record OR normalize_ts > summarize_ts
    """

    def __init__(self, state_path: Path) -> None:
        self._path = state_path
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    self._data = json.load(f)
            else:
                self._data = {}
        return self._data

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get_timestamp(self, phase: str, date_str: str) -> datetime | None:
        """Return the stored timestamp for a phase+date, or None."""
        data = self._load()
        ts_str = data.get(date_str, {}).get(phase)
        if ts_str is None:
            return None
        return datetime.fromisoformat(ts_str)

    def set_timestamp(self, phase: str, date_str: str, ts: datetime | None = None) -> None:
        """Set timestamp for a phase+date and persist immediately."""
        data = self._load()
        if ts is None:
            ts = datetime.now(timezone.utc)
        if date_str not in data:
            data[date_str] = {}
        data[date_str][phase] = ts.isoformat()
        logger.debug("set_timestamp: %s %s → %s", phase, date_str, ts.isoformat())
        self._save()

    def is_fetch_stale(self, date_str: str) -> bool:
        """Fetch is stale if no record OR fetched_at.date() <= target_date."""
        fetch_ts = self.get_timestamp("fetch", date_str)
        if fetch_ts is None:
            logger.debug("is_fetch_stale(%s): True (no record)", date_str)
            return True
        target = date.fromisoformat(date_str)
        result = fetch_ts.date() <= target
        logger.debug("is_fetch_stale(%s): %s (fetched=%s)", date_str, result, fetch_ts.date())
        return result

    def is_normalize_stale(self, date_str: str) -> bool:
        """Normalize is stale if no record OR fetch_ts > normalize_ts."""
        norm_ts = self.get_timestamp("normalize", date_str)
        if norm_ts is None:
            logger.debug("is_normalize_stale(%s): True (no record)", date_str)
            return True
        fetch_ts = self.get_timestamp("fetch", date_str)
        if fetch_ts is None:
            logger.debug("is_normalize_stale(%s): True (no fetch record)", date_str)
            return True
        result = fetch_ts > norm_ts
        logger.debug("is_normalize_stale(%s): %s", date_str, result)
        return result

    def is_summarize_stale(self, date_str: str) -> bool:
        """Summarize is stale if no record OR normalize_ts > summarize_ts."""
        summ_ts = self.get_timestamp("summarize", date_str)
        if summ_ts is None:
            logger.debug("is_summarize_stale(%s): True (no record)", date_str)
            return True
        norm_ts = self.get_timestamp("normalize", date_str)
        if norm_ts is None:
            logger.debug("is_summarize_stale(%s): True (no normalize record)", date_str)
            return True
        result = norm_ts > summ_ts
        logger.debug("is_summarize_stale(%s): %s", date_str, result)
        return result

    def stale_dates(self, phase: str, dates: list[str]) -> list[str]:
        """Filter dates to only those that are stale for the given phase."""
        checker = {
            "fetch": self.is_fetch_stale,
            "normalize": self.is_normalize_stale,
            "summarize": self.is_summarize_stale,
        }
        fn = checker[phase]
        result = [d for d in dates if fn(d)]
        logger.debug("stale_dates(%s): %d/%d stale", phase, len(result), len(dates))
        return result
