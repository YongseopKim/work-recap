"""Tests for FailedDateStore: tracks failed dates for automatic retry on next run.

FailedDateStore solves the problem where a long-range fetch (e.g., 10 years of history)
hits transient rate limits that cause some dates to fail. Without tracking:
  - Failed dates are only reported, not remembered → next run won't retry them
  - No distinction between retryable (timeout, 429) vs permanent (404, 403 non-rate-limit)
  - No visibility into retry exhaustion

Design mirrors DailyStateStore: JSON file, threading.RLock, _load/_save.
"""

import pytest


@pytest.fixture
def store(tmp_path):
    from workrecap.services.failed_dates import FailedDateStore

    return FailedDateStore(tmp_path / "state" / "failed_dates.json", max_retries=3)


class TestFailedDateStoreBasics:
    def test_record_failure_creates_entry(self, store):
        """First failure for a date creates tracking entry with attempt=1."""
        store.record_failure("2025-01-15", "fetch", "Rate limit exceeded")
        entry = store.get_entry("2025-01-15")
        assert entry is not None
        assert entry["phase"] == "fetch"
        assert entry["attempts"] == 1
        assert entry["last_error"] == "Rate limit exceeded"
        assert entry["permanent"] is False

    def test_record_failure_increments_attempts(self, store):
        """Each failure increments the attempt counter for the same date."""
        store.record_failure("2025-01-15", "fetch", "Timeout")
        store.record_failure("2025-01-15", "fetch", "Rate limit")
        entry = store.get_entry("2025-01-15")
        assert entry["attempts"] == 2
        assert entry["last_error"] == "Rate limit"  # updated to latest error

    def test_record_success_removes_entry(self, store):
        """Successful processing clears the failure record."""
        store.record_failure("2025-01-15", "fetch", "Timeout")
        store.record_success("2025-01-15", "fetch")
        assert store.get_entry("2025-01-15") is None

    def test_record_success_noop_when_no_failure(self, store):
        """Clearing a date that never failed is a no-op."""
        store.record_success("2025-01-15", "fetch")  # should not raise

    def test_record_permanent_failure(self, store):
        """Permanent failures (e.g., 404) are marked immediately."""
        store.record_failure("2025-01-15", "fetch", "404 Not Found", permanent=True)
        entry = store.get_entry("2025-01-15")
        assert entry["permanent"] is True

    def test_persistence_across_instances(self, tmp_path):
        """Data persists to disk and survives new instance creation."""
        from workrecap.services.failed_dates import FailedDateStore

        path = tmp_path / "state" / "failed_dates.json"
        store1 = FailedDateStore(path, max_retries=3)
        store1.record_failure("2025-01-15", "fetch", "Error")

        store2 = FailedDateStore(path, max_retries=3)
        entry = store2.get_entry("2025-01-15")
        assert entry is not None
        assert entry["attempts"] == 1


class TestRetryableAndExhausted:
    def test_retryable_dates_filters_within_max_retries(self, store):
        """Dates with attempts < max_retries and not permanent are retryable."""
        store.record_failure("2025-01-15", "fetch", "Timeout")  # 1 attempt
        store.record_failure("2025-01-16", "fetch", "Timeout")  # 1 attempt

        result = store.retryable_dates(["2025-01-15", "2025-01-16", "2025-01-17"])
        assert sorted(result) == ["2025-01-15", "2025-01-16"]

    def test_retryable_dates_excludes_exhausted(self, store):
        """Dates that hit max_retries are NOT retryable."""
        for _ in range(3):
            store.record_failure("2025-01-15", "fetch", "Timeout")
        # 3 attempts = max_retries → exhausted
        result = store.retryable_dates(["2025-01-15"])
        assert result == []

    def test_retryable_dates_excludes_permanent(self, store):
        """Permanent failures are never retryable regardless of attempt count."""
        store.record_failure("2025-01-15", "fetch", "404 Not Found", permanent=True)
        result = store.retryable_dates(["2025-01-15"])
        assert result == []

    def test_exhausted_dates_returns_max_retries_exceeded(self, store):
        """exhausted_dates returns dates that hit the retry limit."""
        for _ in range(3):
            store.record_failure("2025-01-15", "fetch", "Timeout")
        store.record_failure("2025-01-16", "fetch", "Error")  # only 1 attempt

        exhausted = store.exhausted_dates()
        assert exhausted == ["2025-01-15"]

    def test_exhausted_dates_includes_permanent(self, store):
        """Permanent failures are also exhausted (will never be retried)."""
        store.record_failure("2025-01-15", "fetch", "404", permanent=True)
        exhausted = store.exhausted_dates()
        assert exhausted == ["2025-01-15"]


class TestIsPermanentError:
    """Tests for _is_permanent_error: classifies errors for retry eligibility.

    GitHub errors fall into two categories:
    - Permanent: 404 (repo deleted), 403 non-rate-limit (permission), 422 (validation)
      → retrying won't help, save the user time
    - Retryable: timeout, 429, 5xx, network errors
      → transient, should be retried with backoff
    """

    def test_404_is_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Client error 404: /repos/org/repo - Not found") is True

    def test_403_non_rate_limit_is_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Client error 403: Resource not accessible") is True

    def test_422_is_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Client error 422: Validation failed") is True

    def test_403_rate_limit_is_not_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Rate limit exceeded after 7 retries") is False

    def test_429_is_not_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Rate limit exceeded") is False

    def test_500_is_not_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Server error 500 after 3 retries") is False

    def test_timeout_is_not_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Request failed after 3 retries: timeout") is False

    def test_network_error_is_not_permanent(self):
        from workrecap.services.failed_dates import _is_permanent_error

        assert _is_permanent_error("Connection refused") is False
