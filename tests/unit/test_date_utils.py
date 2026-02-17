"""date_utils 유틸리티 함수 테스트."""

from datetime import date
from unittest.mock import patch


from git_recap.services.date_utils import (
    catchup_range,
    date_range,
    monthly_chunks,
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


class TestMonthlyChunks:
    def test_single_month_full(self):
        """전체 1개월."""
        result = monthly_chunks("2026-02-01", "2026-02-28")
        assert result == [("2026-02-01", "2026-02-28")]

    def test_partial_start_and_end(self):
        """시작/끝이 월 중간."""
        result = monthly_chunks("2020-03-15", "2020-05-10")
        assert result == [
            ("2020-03-15", "2020-03-31"),
            ("2020-04-01", "2020-04-30"),
            ("2020-05-01", "2020-05-10"),
        ]

    def test_cross_year(self):
        """연도 경계."""
        result = monthly_chunks("2025-11-15", "2026-01-20")
        assert result == [
            ("2025-11-15", "2025-11-30"),
            ("2025-12-01", "2025-12-31"),
            ("2026-01-01", "2026-01-20"),
        ]

    def test_leap_year_february(self):
        """윤년 2월."""
        result = monthly_chunks("2024-02-01", "2024-02-29")
        assert result == [("2024-02-01", "2024-02-29")]

    def test_leap_year_cross_feb(self):
        """윤년 2월 포함 범위."""
        result = monthly_chunks("2024-01-15", "2024-03-10")
        assert result == [
            ("2024-01-15", "2024-01-31"),
            ("2024-02-01", "2024-02-29"),
            ("2024-03-01", "2024-03-10"),
        ]

    def test_empty_range(self):
        """since > until → 빈 리스트."""
        result = monthly_chunks("2026-03-01", "2026-02-28")
        assert result == []

    def test_single_day(self):
        """1일 범위."""
        result = monthly_chunks("2026-02-15", "2026-02-15")
        assert result == [("2026-02-15", "2026-02-15")]

    def test_same_month_partial(self):
        """같은 달 내 부분 범위."""
        result = monthly_chunks("2026-02-10", "2026-02-20")
        assert result == [("2026-02-10", "2026-02-20")]

    def test_six_years_produces_72_chunks(self):
        """6년 = 72 chunks."""
        result = monthly_chunks("2020-01-01", "2025-12-31")
        assert len(result) == 72
        assert result[0] == ("2020-01-01", "2020-01-31")
        assert result[-1] == ("2025-12-01", "2025-12-31")

    def test_end_of_month_boundary(self):
        """월말이 정확히 until."""
        result = monthly_chunks("2026-01-01", "2026-01-31")
        assert result == [("2026-01-01", "2026-01-31")]

    def test_start_first_end_last_of_different_months(self):
        """1일~말일 완전한 2개월."""
        result = monthly_chunks("2026-01-01", "2026-02-28")
        assert result == [
            ("2026-01-01", "2026-01-31"),
            ("2026-02-01", "2026-02-28"),
        ]
