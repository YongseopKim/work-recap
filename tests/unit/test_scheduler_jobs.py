"""스케줄러 job 함수 테스트 -- daily, weekly, monthly, yearly."""

import asyncio
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.jobs import run_daily_job, run_monthly_job, run_weekly_job, run_yearly_job
from workrecap.scheduler.notifier import LogNotifier

_PATCH_CONFIG = "workrecap.scheduler.jobs.AppConfig"


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "history.json")


@pytest.fixture()
def notifier():
    return LogNotifier()


@pytest.fixture()
def schedule_config():
    return ScheduleConfig(enabled=True)


class TestRunDailyJob:
    def test_runs_yesterday_pipeline(self, tmp_path, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=MagicMock()),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_orch.run_daily.assert_called_once_with(yesterday, types=None)
        entries = history.list()
        assert len(entries) == 1
        assert entries[0]["status"] == "success"
        assert entries[0]["target"] == yesterday

    def test_records_failure(self, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.side_effect = Exception("boom")

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        entries = history.list()
        assert entries[0]["status"] == "failed"
        assert "boom" in entries[0]["error"]

    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"
        mock_summarizer = MagicMock()

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_summarizer.telegram_summary.assert_called_once_with("daily", yesterday)

    def test_telegram_summary_failure_does_not_break_job(
        self, tmp_path, history, notifier, schedule_config
    ):
        mock_orch = MagicMock()
        mock_orch.run_daily.return_value = tmp_path / "summary.md"
        mock_summarizer = MagicMock()
        mock_summarizer.telegram_summary.side_effect = Exception("LLM down")

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_orchestrator", return_value=mock_orch),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_daily_job(schedule_config, history, notifier))

        entries = history.list()
        assert entries[0]["status"] == "success"


class TestRunWeeklyJob:
    def test_runs_last_week_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.weekly.return_value = tmp_path / "W08.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_weekly_job(schedule_config, history, notifier))

        mock_summarizer.weekly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "weekly"

    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.weekly.return_value = tmp_path / "W08.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_weekly_job(schedule_config, history, notifier))

        last_week = date.today() - timedelta(weeks=1)
        iso_year, iso_week, _ = last_week.isocalendar()
        target = f"{iso_year}-W{iso_week:02d}"
        mock_summarizer.telegram_summary.assert_called_once_with("weekly", target)


class TestRunMonthlyJob:
    def test_runs_last_month_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.monthly.return_value = tmp_path / "02.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_monthly_job(schedule_config, history, notifier))

        mock_summarizer.monthly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "monthly"

    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.monthly.return_value = tmp_path / "02.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_monthly_job(schedule_config, history, notifier))

        mock_summarizer.telegram_summary.assert_called_once()
        call_args = mock_summarizer.telegram_summary.call_args[0]
        assert call_args[0] == "monthly"


class TestRunYearlyJob:
    def test_runs_last_year_summary(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.yearly.return_value = tmp_path / "yearly.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"
        mock_summarizer.monthly.return_value = tmp_path / "M.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_yearly_job(schedule_config, history, notifier))

        mock_summarizer.yearly.assert_called_once()
        entries = history.list()
        assert entries[0]["status"] == "success"
        assert entries[0]["job"] == "yearly"

    def test_calls_telegram_summary_on_success(self, tmp_path, history, notifier, schedule_config):
        mock_summarizer = MagicMock()
        mock_summarizer.yearly.return_value = tmp_path / "yearly.md"
        mock_summarizer.weekly.return_value = tmp_path / "W.md"
        mock_summarizer.monthly.return_value = tmp_path / "M.md"

        with (
            patch(_PATCH_CONFIG, return_value=MagicMock()),
            patch("workrecap.scheduler.jobs._build_summarizer", return_value=mock_summarizer),
        ):
            asyncio.run(run_yearly_job(schedule_config, history, notifier))

        mock_summarizer.telegram_summary.assert_called_once()
        call_args = mock_summarizer.telegram_summary.call_args[0]
        assert call_args[0] == "yearly"
