"""Per-date failure tracking for automatic retry on subsequent runs.

Problem: When fetching 10 years of GitHub history (~4,000 dates), transient failures
(rate limits, timeouts, 5xx) cause some dates to fail. Without tracking:
  - Next run doesn't know which dates failed → starts from scratch or skips them
  - No distinction between retryable (will work on retry) vs permanent (never will)
  - No limit on retry attempts → infinite retry loops on persistent issues

Solution: FailedDateStore persists per-date failure info to JSON, enabling:
  - Automatic retry of failed dates on the next run (merged with stale dates)
  - Permanent error detection (404, 403 non-rate-limit, 422) → skip immediately
  - Exhaustion tracking (max_retries reached) → report to user, stop retrying

Follows the same pattern as DailyStateStore: JSON file, threading.RLock, _load/_save.
"""

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Pattern for HTTP status codes in error messages from FetchError
_STATUS_CODE_RE = re.compile(r"(?:Client error|Server error)\s+(\d{3})")


def _is_permanent_error(error_msg: str) -> bool:
    """Classify whether a fetch error is permanent (never worth retrying).

    Permanent errors:
      - 404: repo deleted, PR not found — will never succeed
      - 403 (non-rate-limit): permission denied — retrying won't grant access
      - 422: validation error — malformed request, won't fix itself

    Retryable errors (everything else):
      - Rate limit exceeded (429, 403+rate-limit) — wait and retry
      - Server errors (5xx) — transient infrastructure issues
      - Timeouts, connection errors — network glitches

    This saves significant time in long runs: a deleted repo with 100 PRs
    would waste ~200 API calls on each retry without permanent classification.
    """
    # Rate limit messages should never be permanent
    if "rate limit" in error_msg.lower():
        return False

    match = _STATUS_CODE_RE.search(error_msg)
    if match:
        status = int(match.group(1))
        return status in (404, 403, 422)

    return False


class FailedDateStore:
    """Tracks failed dates with retry metadata for automatic recovery.

    Data structure per date:
      {
        "phase": "fetch",           # pipeline phase that failed
        "attempts": 2,              # total failure count
        "last_error": "...",        # most recent error message
        "last_attempt": "ISO8601",  # when the last attempt happened
        "first_failure": "ISO8601", # when this date first failed
        "permanent": false          # if true, never retry
      }

    Thread-safe via RLock (consistent with DailyStateStore pattern).
    """

    def __init__(self, state_path: Path, *, max_retries: int = 5) -> None:
        self._path = state_path
        self._max_retries = max_retries
        self._data: dict | None = None
        self._lock = threading.RLock()

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

    def record_failure(
        self, date_str: str, phase: str, error: str, *, permanent: bool = False
    ) -> None:
        """Record a failure for the given date. Increments attempt counter."""
        with self._lock:
            data = self._load()
            now = datetime.now(timezone.utc).isoformat()

            if date_str in data:
                entry = data[date_str]
                entry["attempts"] += 1
                entry["last_error"] = error
                entry["last_attempt"] = now
                if permanent:
                    entry["permanent"] = True
            else:
                data[date_str] = {
                    "phase": phase,
                    "attempts": 1,
                    "last_error": error,
                    "last_attempt": now,
                    "first_failure": now,
                    "permanent": permanent,
                }

            logger.debug(
                "record_failure: %s phase=%s attempts=%d permanent=%s",
                date_str,
                phase,
                data[date_str]["attempts"],
                data[date_str]["permanent"],
            )
            self._save()

    def record_success(self, date_str: str, phase: str) -> None:
        """Clear failure record for a date that succeeded."""
        with self._lock:
            data = self._load()
            if date_str in data:
                del data[date_str]
                logger.debug(
                    "record_success: %s phase=%s — cleared failure record", date_str, phase
                )
                self._save()

    def get_entry(self, date_str: str) -> dict | None:
        """Get the failure entry for a date, or None if no failure recorded."""
        with self._lock:
            data = self._load()
            return data.get(date_str)

    def retryable_dates(self, candidates: list[str]) -> list[str]:
        """Filter candidates to only those with recorded failures that are retryable.

        A date is retryable if:
          - It has a failure record (dates with no record are not included)
          - attempts < max_retries
          - Not marked as permanent
        """
        with self._lock:
            data = self._load()
            result = []
            for d in candidates:
                entry = data.get(d)
                if entry is None:
                    continue
                if entry.get("permanent", False):
                    continue
                if entry["attempts"] >= self._max_retries:
                    continue
                result.append(d)
            return result

    def exhausted_dates(self) -> list[str]:
        """Return dates that have exhausted retries or are permanent.

        These dates will never be retried automatically.
        Useful for reporting to the user at the end of a run.
        """
        with self._lock:
            data = self._load()
            result = []
            for d, entry in sorted(data.items()):
                if entry.get("permanent", False) or entry["attempts"] >= self._max_retries:
                    result.append(d)
            return result
