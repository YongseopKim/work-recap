"""date_utils 유틸리티 함수 테스트."""

from datetime import date
from unittest.mock import patch

import pytest

from git_recap.services.date_utils import (
    catchup_range,
    date_range,
    monthly_range,
    weekly_range,
    yearly_range,
)


class TestDateRange:
    def test_single_day(self):
        assert date_range("2026-02-16", "2026-02-16") == ["2026-02-16"]

    def test_multiple_days(self):
        result = date_range("2026-02-14", "2026-02-17")
        assert result == ["2026-02-14", "2026-02-15", "2026-02-16", "2026-02-17"]

    def test_empty_when_since_after_until(self):
        assert date_range("2026-02-17", "2026-02-16") == []

    def test_cross_month_boundary(self):
        result = date_range("2026-01-30", "2026-02-02")
        assert result == ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"]


class TestWeeklyRange:
    def test_iso_week_1_2026(self):
        since, until = weekly_range(2026, 1)
        assert since == "2025-12-29"
        assert until == "2026-01-04"

    def test_iso_week_7_2026(self):
        since, until = weekly_range(2026, 7)
        assert since == "2026-02-09"
        assert until == "2026-02-15"

    def test_week_53_2020(self):
        """2020 has ISO week 53."""
        since, until = weekly_range(2020, 53)
        assert since == "2020-12-28"
        assert until == "2021-01-03"


class TestMonthlyRange:
    def test_february_non_leap(self):
        since, until = monthly_range(2025, 2)
        assert since == "2025-02-01"
        assert until == "2025-02-28"

    def test_february_leap(self):
        since, until = monthly_range(2024, 2)
        assert since == "2024-02-01"
        assert until == "2024-02-29"

    def test_january(self):
        since, until = monthly_range(2026, 1)
        assert since == "2026-01-01"
        assert until == "2026-01-31"

    def test_december(self):
        since, until = monthly_range(2026, 12)
        assert since == "2026-12-01"
        assert until == "2026-12-31"


class TestYearlyRange:
    def test_2026(self):
        since, until = yearly_range(2026)
        assert since == "2026-01-01"
        assert until == "2026-12-31"

    def test_2024(self):
        since, until = yearly_range(2024)
        assert since == "2024-01-01"
        assert until == "2024-12-31"


class TestCatchupRange:
    @patch("git_recap.services.date_utils.date")
    def test_last_fetch_yesterday(self, mock_date):
        mock_date.today.return_value = date(2026, 2, 17)
        mock_date.fromisoformat = date.fromisoformat
        since, until = catchup_range("2026-02-16")
        assert since == "2026-02-17"
        assert until == "2026-02-17"

    @patch("git_recap.services.date_utils.date")
    def test_last_fetch_three_days_ago(self, mock_date):
        mock_date.today.return_value = date(2026, 2, 17)
        mock_date.fromisoformat = date.fromisoformat
        since, until = catchup_range("2026-02-14")
        assert since == "2026-02-15"
        assert until == "2026-02-17"

    @patch("git_recap.services.date_utils.date")
    def test_last_fetch_is_today_returns_empty(self, mock_date):
        mock_date.today.return_value = date(2026, 2, 17)
        mock_date.fromisoformat = date.fromisoformat
        since, until = catchup_range("2026-02-17")
        # since > until means nothing to fetch
        assert since > until
